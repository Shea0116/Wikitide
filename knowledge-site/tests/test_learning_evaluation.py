"""Feedback evidence validation and offline evaluation resource boundaries."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest

from agent_context import estimate_tokens
from learning_service import assessment_messages, validate_feedback

BASE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('learning_eval', BASE / 'scripts/evaluate_learning.py')
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


class Evaluator:
    model = 'synthetic-evaluator'

    def __init__(self, known_usage=True):
        self.calls = []
        self.known_usage = known_usage

    def assess_learning(self, messages, *, usage):
        self.calls.append(messages)
        if self.known_usage:
            usage.update(prompt_tokens=10, completion_tokens=10)
        answer = json.loads(messages[-1]['content'])['answer']
        return json.dumps({'verdict': 'correct', 'answer_quote': answer[:100], 'reference_status': 'usable',
                           'feedback': '合成反馈。', 'matched_points': ['已回答'], 'missing_points': []})


class LearningEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.dataset = evaluation.load_cases(BASE / 'tests/fixtures/learning_feedback_eval.json')

    def run_eval(self, provider, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return evaluation.evaluate(self.dataset, provider, **kwargs)

    def test_feedback_cannot_quote_a_corrected_answer_the_student_did_not_write(self):
        raw = {'verdict': 'correct', 'answer_quote': '不会自动取消', 'reference_status': 'usable',
               'feedback': '你的判断正确。', 'matched_points': ['不会自动取消'], 'missing_points': []}
        with self.assertRaises(ValueError):
            validate_feedback(json.dumps(raw), answer='会，一个失败后其他请求会自动中断。')

    def test_reference_and_contradiction_states_must_match_the_verdict(self):
        base = {'verdict': 'correct', 'answer_quote': '作答', 'reference_status': 'usable', 'feedback': '反馈。'}
        for changes in ({'reference_status': 'conflicting'}, {'reference_status': 'insufficient'},
                        {'contradictions': ['核心断言相反']}, {'missing_points': ['必要条件未回答']}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_feedback(json.dumps(base | changes), answer='作答')
        valid = validate_feedback(json.dumps(base | {'reference_status': 'conflicting', 'verdict': 'uncertain'}), answer='作答')
        self.assertEqual(valid.verdict, 'uncertain')

    def test_eval_call_cap_and_gold_labels_are_not_sent_to_the_model(self):
        provider = Evaluator()
        report = self.run_eval(provider, max_calls=2)
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(report['summary']['stop_reason'], 'call_limit')
        self.assertEqual(report['summary']['reported_tokens'], 40)
        for messages in provider.calls:
            payload = json.loads(messages[-1]['content'])
            self.assertNotIn('expected_verdicts', payload)
            self.assertNotIn('must_address', payload)
        self.assertTrue(all(item['scores']['human_review']['status'] == 'pending' for item in report['results']))

    def test_missing_usage_is_charged_to_the_reservation_and_stops_next_call(self):
        first = self.dataset['cases'][0]
        budget = estimate_tokens(assessment_messages(first['question'], first['reference_answer'], first['answer'], first['hint_used'])) + 1000
        provider = Evaluator(known_usage=False)
        report = self.run_eval(provider, max_tokens=budget)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(report['summary']['budget_charged_tokens'], budget)
        self.assertEqual(report['summary']['usage_known_calls'], 0)
        self.assertEqual(report['summary']['stop_reason'], 'token_limit')

    def test_provider_failure_stops_the_run_without_recording_exception_text(self):
        class Broken:
            def assess_learning(self, *args, **kwargs):
                raise RuntimeError('SECRET-PROVIDER-ERROR')
        report = self.run_eval(Broken())
        self.assertEqual(report['summary']['calls'], 1)
        self.assertEqual(report['summary']['stop_reason'], 'provider_error')
        self.assertNotIn('SECRET-PROVIDER-ERROR', json.dumps(report))

    def test_invalid_feedback_is_not_counted_as_a_valid_or_agreeing_response(self):
        class Invalid:
            def assess_learning(self, *args, **kwargs):
                return '{"verdict":"correct"}'
        report = self.run_eval(Invalid(), max_calls=1)
        self.assertEqual(report['summary']['valid_responses'], 0)
        self.assertEqual(report['summary']['verdict_agreements'], 0)
        self.assertEqual(report['results'][0]['candidate_text'], '{"verdict":"correct"}')
        self.assertEqual(report['results'][0]['error']['validation_rule'], 'schema_validation')


if __name__ == '__main__':
    unittest.main()
