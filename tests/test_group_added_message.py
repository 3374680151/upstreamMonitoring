import unittest

from backend.repositories.changes import diff_groups


class GroupAddedMessageTests(unittest.TestCase):
    def test_new_group_message_includes_ratio(self):
        changes = diff_groups(
            {},
            {"plus": {"ratio": 1.2, "ratio_type": "number", "desc": "纯血plus"}},
        )

        self.assertEqual(changes[0]["message"], "新增分组 plus · 倍率 1.20x")


if __name__ == "__main__":
    unittest.main()
