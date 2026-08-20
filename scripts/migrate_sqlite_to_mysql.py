#!/usr/bin/env python3
"""一次性迁移：把旧的 SQLite `data/app.db` 数据导入 MySQL。

用法：
    python3 scripts/migrate_sqlite_to_mysql.py [--sqlite data/app.db] [--truncate]

MySQL 连接从环境变量 / .env 读取（DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME），
与后端 app.py 完全一致；不接受任何明文密码参数，避免泄露。

默认用 upsert（INSERT ... ON DUPLICATE KEY UPDATE）写入，可安全重复执行、天然幂等；
init_db 预置的 notification_settings(id=1) 也不会主键冲突。
--truncate：导入前清空 MySQL 同名表（用于让目标与源完全一致，会删除源中已不存在的行）。
迁移完成后会逐表核对「源 SQLite → 目标 MySQL」的行数。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

# 复用 FastAPI 后端的连接层与建表逻辑（连接池会自动加载本地 .env）。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pymysql  # noqa: E402
from backend.core.config import DATA_DIR  # noqa: E402
from backend.db.pool import DB_CONFIG, connect_db  # noqa: E402
from backend.db.schema import init as init_schema  # noqa: E402

# 父表在前，导入按此序；清空按其逆序，满足外键约束。
TABLES = ["sites", "snapshots", "changes", "notification_settings", "notification_logs"]


def mysql_connect() -> "pymysql.connections.Connection":
    return connect_db()


def mysql_columns(conn, table: str) -> list:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
            "ORDER BY ORDINAL_POSITION",
            (table,),
        )
        return [row["COLUMN_NAME"] for row in cur.fetchall()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sqlite", default=str(DATA_DIR / "app.db"), help="源 SQLite 文件路径"
    )
    parser.add_argument("--truncate", action="store_true", help="导入前清空 MySQL 目标表")
    args = parser.parse_args()

    src_path = Path(args.sqlite)
    if not src_path.exists():
        print(f"[x] 源 SQLite 文件不存在：{src_path}")
        return 1

    print(f"[*] 源：{src_path}")
    print(f"[*] 目标 MySQL：{DB_CONFIG['user']}@{DB_CONFIG['host']}:"
          f"{DB_CONFIG['port']}/{DB_CONFIG['database']}")

    # 1) 确保 MySQL 结构存在
    init_schema()

    src = sqlite3.connect(str(src_path))
    src.row_factory = sqlite3.Row

    all_ok = True
    dst = mysql_connect()
    try:
        with dst.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS = 0")

            if args.truncate:
                for table in reversed(TABLES):
                    cur.execute(f"DELETE FROM {table}")
                    print(f"[-] 清空 {table}")

            for table in TABLES:
                # 只搬两边都有的列
                dst_cols = mysql_columns(dst, table)
                try:
                    rows = src.execute(f"SELECT * FROM {table}").fetchall()
                except sqlite3.OperationalError as exc:
                    print(f"[!] 跳过 {table}：源表读取失败 ({exc})")
                    continue
                if not rows:
                    print(f"[=] {table}: 源 0 行")
                    continue

                all_src_cols = list(rows[0].keys())
                src_cols = [c for c in all_src_cols if c in dst_cols]
                dropped = [c for c in all_src_cols if c not in dst_cols]
                if dropped:
                    print(f"[!] {table}: 源有、目标无的列将被忽略：{', '.join(dropped)}")
                placeholders = ", ".join(["%s"] * len(src_cols))
                collist = ", ".join(f"`{c}`" for c in src_cols)
                update_clause = ", ".join(f"`{c}`=VALUES(`{c}`)" for c in src_cols)
                # 幂等 upsert：init_db 预置的 notification_settings(id=1) 与重复执行都不会主键冲突
                sql = (
                    f"INSERT INTO {table} ({collist}) VALUES ({placeholders}) "
                    f"ON DUPLICATE KEY UPDATE {update_clause}"
                )
                data = [tuple(row[c] for c in src_cols) for row in rows]
                cur.executemany(sql, data)
                print(f"[*] {table}: 待写入 {len(data)} 行（提交后校验）")

            cur.execute("SET FOREIGN_KEY_CHECKS = 1")
        dst.commit()

        # 提交后逐表核对行数，避免「打印成功却整体回滚」的假象
        print("[*] 校验行数（源 SQLite → 目标 MySQL）：")
        with dst.cursor() as cur:
            for table in TABLES:
                try:
                    src_n = src.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
                except sqlite3.OperationalError:
                    continue
                cur.execute(f"SELECT COUNT(*) AS n FROM {table}")
                dst_n = cur.fetchone()["n"]
                ok = dst_n >= src_n
                all_ok = all_ok and ok
                print(f"    {'✓' if ok else '✗'} {table}: 源 {src_n} → 目标 {dst_n}")
    finally:
        dst.close()
        src.close()

    if all_ok:
        print("[✓] 迁移完成，行数校验通过")
        return 0
    print("[!] 迁移完成，但部分表目标行数少于源，请检查上面的 ✗ 项")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
