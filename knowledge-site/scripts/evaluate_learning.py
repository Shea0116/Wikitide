"""Frozen synthetic diagnostics for the production learning-feedback contract."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from agent_context import estimate_tokens
from learning_service import (FEEDBACK_RULES, PROMPT_VERSION, Feedback,
                              assessment_messages, validate_feedback)


def sha(value):
    return hashlib.sha256(value).hexdigest()


def load_cases(path):
    dataset = json.loads(path.read_text())
    cases = dataset['cases']
    if not cases or len({case['id'] for case in cases}) != len(cases):
        raise ValueError('Case IDs must be present and unique')
    for case in cases:
        if (any(not isinstance(case[key], str) or not case[key].strip() for key in ('id', 'category', 'question', 'reference_answer', 'answer'))
            or type(case['hint_used']) is not bool or not case['expected_verdicts']
            or not set(case['expected_verdicts']).issubset({'correct', 'partial', 'needs_review', 'uncertain'})
            or any(not group or any(not isinstance(term, str) or not term for term in group) for group in case['must_address'])):
            raise ValueError('Invalid diagnostic case')
    return dataset


def score(case, feedback):
    text = '\n'.join([feedback.feedback, *feedback.matched_points, *feedback.missing_points, *feedback.contradictions]).casefold()
    checks = [any(term.casefold() in text for term in group) for group in case['must_address']]
    return {'verdict_agrees_with_fixture': feedback.verdict in case['expected_verdicts'],
            'lexical_checks_passed': sum(checks), 'lexical_checks_total': len(checks),
            'human_review': {'status': 'pending', 'supports_reference': None, 'identifies_error': None, 'actionable_feedback': None}}


def evaluate(dataset, provider, *, max_calls=20, max_tokens=60000, repetitions=1):
    results, charged = [], 0
    stop_reason = 'completed'
    for repeat in range(repetitions):
        for case in dataset['cases']:
            messages = assessment_messages(case['question'], case['reference_answer'], case['answer'], case['hint_used'])
            reservation = estimate_tokens(messages) + 1000
            if len(results) >= max_calls or charged + reservation > max_tokens:
                stop_reason = 'call_limit' if len(results) >= max_calls else 'token_limit'
                break
            usage, started = {}, time.perf_counter()
            item = {'id': case['id'], 'repeat': repeat + 1, 'category': case['category'],
                    'expected_verdicts': case['expected_verdicts'], 'status': 'failed', 'reservation': reservation}
            provider_completed = False
            try:
                raw = provider.assess_learning(messages, usage=usage)
                provider_completed = True
                feedback = validate_feedback(raw, answer=case['answer'])
                item.update(status='valid', feedback=feedback.model_dump(), scores=score(case, feedback))
            except Exception as exc:
                # Never expose provider bodies, headers, keys or raw exception text.
                item['error'] = {'type': type(exc).__name__, 'http_status': getattr(exc, 'status_code', None)}
                if not provider_completed:
                    stop_reason = 'provider_error'
                else:
                    item['candidate_text'] = raw[:12000] if isinstance(raw, str) else None
                    rules = {'Feedback quote is not in the submitted answer': 'quote_not_exact',
                             'Unusable reference cannot yield a correctness verdict': 'reference_verdict_conflict',
                             'Contradictory answer cannot be marked correct or partial': 'contradiction_verdict_conflict',
                             'A correct answer cannot have missing required points': 'missing_points_with_correct_verdict',
                             'Feedback point exceeds limit': 'invalid_point'}
                    item['error']['validation_rule'] = rules.get(str(exc), 'schema_validation')
            a, b = usage.get('prompt_tokens'), usage.get('completion_tokens')
            known = type(a) is int and a >= 0 and type(b) is int and b >= 0
            item['usage'] = {'prompt_tokens': a if known else None, 'completion_tokens': b if known else None,
                             'total_tokens': a + b if known else None}
            item['duration_ms'] = round((time.perf_counter() - started) * 1000)
            charged += a + b if known else reservation
            results.append(item)
            print(json.dumps({'id': item['id'], 'repeat': item['repeat'], 'status': item['status'],
                              'verdict': item.get('feedback', {}).get('verdict')}, ensure_ascii=False), flush=True)
            if stop_reason != 'completed':
                break
        if stop_reason != 'completed':
            break
    valid = [item for item in results if item['status'] == 'valid']
    durations = sorted(item['duration_ms'] for item in results)
    agreements = sum(item['scores']['verdict_agrees_with_fixture'] for item in valid)
    groups = {}
    for category in sorted({case['category'] for case in dataset['cases']}):
        subset = [item for item in results if item['category'] == category]
        groups[category] = {'calls': len(subset), 'valid': sum(item['status'] == 'valid' for item in subset),
                            'verdict_agreements': sum(item.get('scores', {}).get('verdict_agrees_with_fixture', False) for item in subset)}
    return {'created_at': datetime.now(timezone.utc).isoformat(), 'model': str(getattr(provider, 'model', 'unknown')),
            'prompt_version': PROMPT_VERSION, 'prompt_sha256': sha(FEEDBACK_RULES.encode()),
            'schema_sha256': sha(json.dumps(Feedback.model_json_schema(), sort_keys=True).encode()),
            'limits': {'max_calls': max_calls, 'max_tokens': max_tokens, 'repetitions': repetitions, 'output_tokens_per_call': 1000},
            'summary': {'calls': len(results), 'valid_responses': len(valid), 'verdict_agreements': agreements,
                        'verdict_agreement_over_all_calls': agreements / len(results) if results else None,
                        'verdict_counts': dict(Counter(item['feedback']['verdict'] for item in valid)),
                        'reported_tokens': sum(item['usage']['total_tokens'] or 0 for item in results),
                        'usage_known_calls': sum(item['usage']['total_tokens'] is not None for item in results),
                        'budget_charged_tokens': charged, 'stop_reason': stop_reason,
                        'p50_ms': durations[(len(durations) - 1) // 2] if durations else None,
                        'p95_ms': durations[min(len(durations) - 1, int(len(durations) * .95))] if durations else None},
            'by_category': groups, 'results': results,
            'limitations': ['Synthetic cases and expected labels were authored by the agent; human reference review is pending.',
                            'Verdict agreement and lexical mentions do not establish semantic correctness or learning improvement.',
                            'This is the first frozen diagnostic set, not a repeated or population-level study.',
                            'Once used to change a prompt, these cases are development diagnostics rather than an independent holdout.',
                            'A completed upstream call can exceed an estimated input reservation; the next call is stopped by the remaining budget.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--real', action='store_true', help='Permit billable calls using synthetic data only')
    parser.add_argument('--dataset', type=Path, default=BASE / 'tests/fixtures/learning_feedback_eval.json')
    parser.add_argument('--validate-only', action='store_true')
    parser.add_argument('--role', choices=['diagnostic', 'development_comparison', 'holdout'], default='diagnostic')
    parser.add_argument('--max-calls', type=int, default=20)
    parser.add_argument('--max-tokens', type=int, default=60000)
    parser.add_argument('--repetitions', type=int, default=1)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    dataset = load_cases(args.dataset)
    fingerprint = sha(args.dataset.read_bytes())
    if args.validate_only:
        print(json.dumps({'cases': len(dataset['cases']), 'dataset_sha256': fingerprint})); return 0
    if not args.real or not args.output:
        parser.error('Use --real and --output to run model diagnostics')
    if args.max_calls < 1 or args.max_tokens < 1000 or args.repetitions < 1:
        parser.error('Positive call/repetition limits and at least 1000 tokens are required')
    from explore import DeepSeek
    provider = DeepSeek()
    if not provider.ready:
        parser.error('The existing provider is not configured')
    report = evaluate(dataset, provider, max_calls=args.max_calls, max_tokens=args.max_tokens, repetitions=args.repetitions)
    report.update(schema_version=1, dataset_id=dataset['dataset_id'], dataset_sha256=fingerprint,
                  dataset_human_reviewed=dataset.get('human_reviewed', False), evaluation_role=args.role)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report['summary'], ensure_ascii=False))
    return int(report['summary']['valid_responses'] != report['summary']['calls'] or report['summary']['stop_reason'] != 'completed')


if __name__ == '__main__':
    raise SystemExit(main())
