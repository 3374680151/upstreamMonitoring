# 一键部署优化方案(提案,未实施)

> 依据:2026-09-05 两轮全流程走查(见 deploy-walkthrough-issues-待修复.md)。
> 原则:分三层推进,第一层零接口契约变更、纯新增文件,收益最大;第三层涉及行为/接口变更,按规范设计先行 + Apifox 确认后再做。
> 所有代码均为审阅稿,确认后才落入仓库。

## 客户现状(走查实测的痛点,按顺序)

| # | 摩擦点 | 证据 |
|---|--------|------|
| 1 | 系统 Python 没依赖,README 没提 venv(仓库里的 .venv 是本机手工建的,新 clone 没有) | 走查实测 `ModuleNotFoundError: No module named 'fastapi'` |
| 2 | `.env` 必须手工编辑:DB_PASSWORD 不填 compose 直接拒启;控制台密码也要进 .env | `docker-compose.yml:8`;登录页 placeholder「在服务端 .env 中设置」 |
| 3 | 裸机路径要手敲 `CREATE DATABASE`(全仓库无自动建库) | 走查实测 |
| 4 | 前端构建要装 Node ≥ 20(Docker 路径不需要) | Dockerfile 多阶段构建 |
| 5 | 宿主机 3306 被占时 compose 报错,要自己想到改 DB_PORT=3307 | README 只有一行小字提醒 |
| 6 | 扩展三重摩擦:开发者模式手动加载、去仓库翻目录、只支持 localhost 访问 | manifest.json;popup 写死 127.0.0.1:8000 |
| 7 | 装完不知道下一步干嘛(添加站点?装不装扩展?通知怎么配?) | 走查全程无引导 |
| 8 | `VITE_CONTACT_WECHAT` 构建期变量未记载 | AppShell.vue:27 |

## 目标体验

```bash
git clone <repo> && cd upstream
bash scripts/setup.sh            # Docker 或裸机自动分流,一条命令到底
# → 打印访问地址、登录密码、扩展安装两步、下一步指引
```

---

## 第一层:scripts/setup.sh(纯新增,零契约变更,建议先做)

设计决策(每条都来自走查实测):

1. **.env 自动生成**:随机 DB 密码(`openssl rand` / `secrets` 兜底),控制台密码交互式静默输入(可回车跳过=不启用鉴权);已存在不覆盖,`--reset-env` 才重建。
2. **Docker 优先**:检测到 Docker 就整栈交给 compose(镜像内已含前端构建、MySQL 自动建库、应用自动 init_db 建表)——这是唯一不需要本机 Node 的路径。
3. **宿主 3306 冲突自动避让**:生成 .env 时若检测到宿主 3306 已有监听者,自动写入 `DB_PORT=3307` 并打印说明(把 README 里那行小字变成自动行为)。
4. **裸机建库不依赖 mysql CLI**:先建 venv 装依赖,再用 `.venv/bin/python + PyMySQL + python-dotenv` 执行 `CREATE DATABASE IF NOT EXISTS`(走查时我就是这么建测试库的,顺带规避客户机器没有 mysql 客户端的问题)。
5. **版本门槛检查**:python ≥ 3.11、node ≥ 20(vite 7 要求),不合格直接说人话报错。
6. **启动后自动 `/healthz` 轮询**,成功后打印「下一步」清单;支持 `--demo` 首启播种示例站点(SEED_DEMO=1,现成能力)体验全流程。
7. 幂等:venv/dist 已存在则跳过,重复执行安全。

```bash
#!/usr/bin/env bash
# scripts/setup.sh — Upstream 一键初始化(审阅稿)
# 用法: bash scripts/setup.sh [--port 8000] [--demo] [--reset-env]
set -euo pipefail

PORT="8000"; DEMO="0"; RESET_ENV="0"
while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="$2"; shift 2;;
    --demo) DEMO="1"; shift;;
    --reset-env) RESET_ENV="1"; shift;;
    *) echo "未知参数: $1"; exit 1;;
  esac
done

log()  { printf '\033[1;32m[setup]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[setup]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[setup]\033[0m %s\n' "$*" >&2; exit 1; }

gen_password() { openssl rand -hex 16 2>/dev/null || python3 -c "import secrets; print(secrets.token_hex(16))"; }

# ── 1) .env ──
if [ -f .env ] && [ "$RESET_ENV" != "1" ]; then
    log ".env 已存在,沿用(重建请加 --reset-env)"
else
    DB_PASSWORD="$(gen_password)"
    printf '设置控制台登录密码(输入时不回显,回车跳过 = 不启用鉴权,仅限本机/内网): '
    read -rs CONSOLE_PASSWORD || CONSOLE_PASSWORD=""
    echo
    cat > .env <<EOF
# 由 scripts/setup.sh 生成
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=root
DB_PASSWORD=${DB_PASSWORD}
DB_NAME=upstream
HOST=127.0.0.1
PORT=${PORT}
APP_TIMEZONE=Asia/Shanghai
SCHEDULER_ENABLED=1
ENABLE_API_DOCS=0
SEED_DEMO=${DEMO}
CONSOLE_PASSWORD=${CONSOLE_PASSWORD}
CONSOLE_SESSION_TTL=604800
EOF
    log ".env 已生成"
fi

set -a; . ./.env; set +a

# ── 2) Docker 路径 ──
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    # 宿主 3306 已被占用(非本 compose)→ 自动避让到 3307
    if [ "${DB_PORT:-3306}" = "3306" ] && lsof -nP -i :3306 2>/dev/null | grep -q LISTEN; then
        warn "宿主机 3306 已被占用,自动改用 DB_PORT=3307"
        sed -i '' 's/^DB_PORT=3306$/DB_PORT=3307/' .env   # Linux 用 sed -i 's/…/'
        set -a; . ./.env; set +a
    fi
    log "检测到 Docker,docker compose 构建并启动(首次构建需几分钟)"
    docker compose up -d --build
    for _ in $(seq 1 60); do
        curl -fsS "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1 && break
        sleep 2
    done
    curl -fsS "http://127.0.0.1:${PORT}/healthz" >/dev/null || die "服务未就绪,查看: docker compose logs upstream"
    RUNTIME="docker"
else
    # ── 3) 裸机路径 ──
    command -v python3 >/dev/null || die "缺少 python3(≥3.11);或安装 Docker 使用容器部署"
    command -v node >/dev/null || die "缺少 node(≥20);或安装 Docker 使用容器部署"
    [ "$(python3 -c 'import sys; print(1 if sys.version_info >= (3,11) else 0)')" = "1" ] || die "python3 需 ≥ 3.11,当前 $(python3 -V)"
    [ -d .venv ] || { log "创建虚拟环境 .venv"; python3 -m venv .venv; }
    log "安装后端依赖"
    .venv/bin/pip install -q -r requirements.txt
    [ -f apps/web/dist/index.html ] || {
        log "构建前端(npm ci + build,几分钟)"
        (cd apps/web && npm ci && npm run build)
    }
    log "创建数据库 ${DB_NAME:-upstream}(如不存在)"
    .venv/bin/python - <<'PY'
import os
from dotenv import load_dotenv
load_dotenv(".env")
import pymysql
conn = pymysql.connect(
    host=os.getenv("DB_HOST", "127.0.0.1"),
    port=int(os.getenv("DB_PORT", "3306")),
    user=os.getenv("DB_USER", "root"),
    password=os.getenv("DB_PASSWORD", ""),
    connect_timeout=5,
)
name = os.getenv("DB_NAME", "upstream")
with conn.cursor() as cur:
    cur.execute(f"CREATE DATABASE IF NOT EXISTS `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
conn.commit(); conn.close()
print("数据库就绪:", name)
PY
    log "后台启动服务(日志: upstream.log)"
    nohup .venv/bin/python app.py > upstream.log 2>&1 &
    for _ in $(seq 1 30); do
        curl -fsS "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1 && break
        sleep 2
    done
    curl -fsS "http://127.0.0.1:${PORT}/healthz" >/dev/null || die "服务未就绪,查看 upstream.log"
    RUNTIME="bare-metal"
fi

cat <<EOF

──────────────────────────────────────────────
✅ 部署完成(${RUNTIME})
   控制台: http://127.0.0.1:${PORT}
   登录密码: 见 .env 的 CONSOLE_PASSWORD(留空 = 未启用鉴权)
下一步:
 1. 打开控制台,「添加渠道」填上游 NewAPI/sub2api 地址即可开始监控
    (纯公开倍率监控不需要任何认证字段;认证增强/登录态同步才需要扩展)
 2. 浏览器同步扩展(可选):chrome://extensions → 开发者模式 →
    加载已解压的扩展程序 → 选择 extensions/upstream-session-bridge 目录
    注意:扩展仅支持通过 http://127.0.0.1 或 localhost 访问控制台
 3. 「消息推送」页配置企业微信 / SMTP 并测试发送
常用命令:
  停止: ${RUNTIME:+docker compose down}  日志: docker compose logs -f upstream
EOF
```

配套改动(同层):
- README「快速开始」重写为脚本入口,手工步骤降级为附录;补 `VITE_CONTACT_WECHAT` 说明;
- `.env.example` 补该变量;
- Windows:后续补 `scripts/setup.ps1`(第二层再说的备注)。

## 第二层:扩展分发 + 控制台引导(不动 /api 契约)

1. **扩展随镜像分发**:Dockerfile 增加 `COPY extensions /app/extensions`;`main.py` 用 `StaticFiles` 挂载扩展目录(如 `/extension`),构建时/启动时用标准库 `zipfile` 打一个 `upstream-session-bridge.zip` 供下载——不新增 router、不进 Apifox 契约。
2. **控制台引导卡片**(纯前端):渠道监控页或独立小页,内容=下载按钮 + 三步安装说明 + 扩展连接状态(复用现有 PROBE 探测,`browserSessionBridge.ts` 已有能力,能显示「已连接/未安装」);未装扩展但站点开了认证增强时给出现场指引。
3. **顺带修指引文案**:明确「仅 localhost/127.0.0.1 可用扩展」的限制。

## 第三层(行为/接口变更,需设计先行 + Apifox 确认)

1. **添加渠道默认值调整**:「认证增强监控」默认 OFF、认证方式默认「公开监控」——把最重的依赖(扩展)从默认路径上摘掉。产品行为变更,需确认。
2. **首启向导**:首次打开控制台(空库+未配置)显示三步引导(设密码提示→添加第一个站点→可选配推送);涉及新 API 的部分单独设计。
3. **Chrome Web Store 上架**:装扩展变真·一键(需开发者账号+审核,长期)。
4. **setup.ps1**(Windows)与「无 Node 裸机」发布产物(如 GitHub Release 附 dist 包)按需评估。

## 实施顺序建议

1. 第一层 script + README/.env.example(半天,立刻消掉痛点 1/2/3/5/8);
2. 第二层扩展下载+引导(1 天内,消掉痛点 6/7 的大头);
3. 第三层按优先级排期(默认值调整建议最先,一行默认值 + UI 文案,收益/成本比最高)。
