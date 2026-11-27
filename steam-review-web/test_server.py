import unittest

from server import answer, local_statistics_intent


class LocalStatisticsTests(unittest.TestCase):
    def test_existing_page_questions(self):
        cases = [
            ("中文玩家和英文玩家的评价差多少？", "language", "5.32 pp"),
            ("这次更新里哪一项改动被提到最多？", "items", "本地分屏合作（Multiplayer changes）· 提及 17 次"),
            ("评论量峰值出现在哪一天？", "trend", "2020-12-22</b>（551 条"),
        ]
        for question, expected_intent, expected_text in cases:
            with self.subTest(question=question):
                intent = local_statistics_intent(question)
                self.assertEqual(intent, expected_intent)
                result = answer("sdv_20201221_15", question, intent)
                self.assertEqual(result["intent"], expected_intent)
                self.assertIn(expected_text, result["answer"])

    def test_agent_questions_stay_with_agent(self):
        for question in (
            "星露谷 1.5 改了什么？",
            "中文玩家怎么评价姜岛探索？",
            "哪个版本发布日推荐率最高？",
            "哪个版本评论量最多？",
            "为什么玩家吐槽旅行图？",
        ):
            with self.subTest(question=question):
                self.assertIsNone(local_statistics_intent(question))


if __name__ == "__main__":
    unittest.main()
