"""Recompute the Documentation CUSUM shadow state.

This command is deliberately isolated from the radar/score engine. It only
writes ``CusumSnapshot`` and ``CusumEpisode`` rows.
"""

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from dashboard.cusum import CusumParameters, bernoulli_cusum
from dashboard.models import (
    CUSUM_MODULE_CHOICES,
    CusumEpisode,
    CusumSnapshot,
    DocumentationLog,
)


DOCUMENTATION_SLA = timedelta(hours=4)
SOURCE_MODEL = 'DocumentationLog'


@dataclass
class OfficerEvaluation:
    officer: User
    valid_logs: list
    outcomes: list
    skipped_count: int
    skipped_ids: list
    result: object
    window_start: object
    window_end: object
    last_log: object
    metadata: dict


def _as_aware(value):
    if value is None:
        return None
    if timezone.is_naive(value):
        return timezone.make_aware(value)
    return value


def _local_date(value):
    if value is None:
        return None
    if timezone.is_aware(value):
        return timezone.localtime(value).date()
    return value.date()


def _day_bounds(as_of):
    start = timezone.make_aware(datetime.combine(as_of, time.min))
    end = timezone.make_aware(datetime.combine(as_of, time.max))
    return start, end


def _evaluate_officer(officer, as_of, since, parameters):
    _, as_of_end = _day_bounds(as_of)
    raw_logs = (
        DocumentationLog.objects
        .filter(officer=officer, is_completed=True)
        .only('id', 'encounter_at', 'completed_at')
        .order_by('encounter_at', 'id')
    )

    valid_logs = []
    outcomes = []
    skipped_count = 0
    skipped_ids = []

    for log in raw_logs:
        encounter_at = _as_aware(log.encounter_at)
        completed_at = _as_aware(log.completed_at)

        if encounter_at is None:
            skipped_count += 1
            skipped_ids.append(log.id)
            continue

        encounter_date = _local_date(encounter_at)
        if encounter_date.year != as_of.year or encounter_date < since:
            continue

        if completed_at is None or completed_at < encounter_at:
            skipped_count += 1
            skipped_ids.append(log.id)
            continue

        if completed_at > as_of_end:
            continue

        valid_logs.append(log)
        outcomes.append((completed_at - encounter_at) > DOCUMENTATION_SLA)

    late_count = sum(1 for outcome in outcomes if outcome)
    on_time_count = len(outcomes) - late_count
    pending_count = (
        DocumentationLog.objects
        .filter(officer=officer, is_completed=False)
        .count()
    )

    result = bernoulli_cusum(outcomes, parameters)
    last_log = valid_logs[-1] if valid_logs else None
    window_start = _as_aware(valid_logs[0].encounter_at) if valid_logs else None
    window_end = _as_aware(last_log.completed_at) if last_log else None

    return OfficerEvaluation(
        officer=officer,
        valid_logs=valid_logs,
        outcomes=outcomes,
        skipped_count=skipped_count,
        skipped_ids=skipped_ids,
        result=result,
        window_start=window_start,
        window_end=window_end,
        last_log=last_log,
        metadata={
            'as_of': as_of.isoformat(),
            'since': since.isoformat(),
            'skipped_ids': skipped_ids[:50],
            'skipped_count': skipped_count,
            'late_count': late_count,
            'on_time_count': on_time_count,
            'pending_count': pending_count,
        },
    )


def _alert_timestamp(evaluation, alert):
    log = evaluation.valid_logs[alert.sequence - 1]
    return _as_aware(log.completed_at)


def _persist_evaluation(officer, module, evaluation, parameters, reset):
    result = evaluation.result
    last_log = evaluation.last_log

    with transaction.atomic():
        if reset:
            CusumSnapshot.objects.filter(
                officer=officer,
                module=module,
            ).delete()
            CusumEpisode.objects.filter(
                officer=officer,
                module=module,
            ).delete()

        snapshot, snapshot_created = CusumSnapshot.objects.update_or_create(
            officer=officer,
            module=module,
            defaults={
                'state': result.state,
                'cusum_value': result.cusum_value,
                'alert_count': result.alert_count,
                'observation_count': result.observation_count,
                'skipped_count': evaluation.skipped_count,
                'last_sequence': result.observation_count,
                'last_event_at': (
                    _as_aware(last_log.completed_at)
                    if last_log else None
                ),
                'last_alert_at': (
                    _alert_timestamp(evaluation, result.alerts[-1])
                    if result.alerts else None
                ),
                'last_alert_number': result.alert_count,
                'window_start': evaluation.window_start,
                'window_end': evaluation.window_end,
                'last_source_model': SOURCE_MODEL,
                'last_source_id': last_log.id if last_log else None,
                'p0': parameters.p0,
                'p1': parameters.p1,
                'h': parameters.h,
                'metadata': evaluation.metadata,
            },
        )

        episode_created_count = 0
        episode_updated_count = 0
        for alert in result.alerts:
            log = evaluation.valid_logs[alert.sequence - 1]
            _, created = CusumEpisode.objects.update_or_create(
                officer=officer,
                module=module,
                source_model=SOURCE_MODEL,
                source_id=log.id,
                defaults={
                    'alert_number': alert.alert_number,
                    'trigger_sequence': alert.sequence,
                    'triggered_at': _as_aware(log.completed_at),
                    'cusum_value': alert.cusum_value,
                    'peak_cusum': alert.cusum_value,
                    'observation_count': alert.observation_count,
                    'status': 'OPEN',
                    'p0': parameters.p0,
                    'p1': parameters.p1,
                    'h': parameters.h,
                    'metadata': {
                        'as_of': evaluation.metadata['as_of'],
                        'source_encounter_at': (
                            _as_aware(log.encounter_at).isoformat()
                            if log.encounter_at else None
                        ),
                        'source_completed_at': (
                            _as_aware(log.completed_at).isoformat()
                            if log.completed_at else None
                        ),
                    },
                },
            )
            if created:
                episode_created_count += 1
            else:
                episode_updated_count += 1

    return snapshot, snapshot_created, episode_created_count, episode_updated_count


def _parse_date(value, option_name):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise CommandError(
            f'{option_name} mesti dalam format YYYY-MM-DD.'
        )


def _round(value):
    return round(float(value), 4)


class Command(BaseCommand):
    help = (
        'Kira semula CUSUM Bernoulli Documentation dalam Shadow Mode. '
        'Command ini tidak mengubah skor radar.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--username',
            type=str,
            default=None,
            help='Username officer yang hendak diproses.',
        )
        parser.add_argument(
            '--all',
            action='store_true',
            help='Proses semua officer yang mempunyai DocumentationLog.',
        )
        parser.add_argument(
            '--module',
            type=str,
            default='documentation',
            help='Modul CUSUM (default: documentation).',
        )
        parser.add_argument(
            '--as-of',
            type=str,
            default=None,
            help='Tarikh rujukan YYYY-MM-DD (default: hari ini).',
        )
        parser.add_argument(
            '--since',
            type=str,
            default=None,
            help='Tarikh mula YYYY-MM-DD (default: 1 Januari tahun as-of).',
        )
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Padam snapshot/episode CUSUM sedia ada sebelum rebuild.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Kira dan papar ringkasan tanpa menulis ke database.',
        )
        parser.add_argument(
            '--json',
            action='store_true',
            help='Output ringkasan dalam format JSON.',
        )
        parser.add_argument(
            '--p0',
            type=float,
            default=None,
            help='Kadar kegagalan baseline (default: 0.20).',
        )
        parser.add_argument(
            '--p1',
            type=float,
            default=None,
            help='Kadar kegagalan out-of-control (default: 0.35).',
        )
        parser.add_argument(
            '--h',
            type=float,
            default=None,
            help='Ambang keputusan CUSUM (default: 4.5).',
        )

    def handle(self, *args, **options):
        module = (options.get('module') or '').strip().lower()
        if module not in dict(CUSUM_MODULE_CHOICES):
            raise CommandError(
                f'--module tidak sah. Pilihan: '
                f'{", ".join(dict(CUSUM_MODULE_CHOICES))}.'
            )

        username = (options.get('username') or '').strip()
        process_all = bool(options.get('all'))
        if bool(username) == process_all:
            raise CommandError(
                'Sila berikan sama ada --username ATAU --all, '
                'bukan kedua-duanya.'
            )

        as_of = (
            _parse_date(options.get('as_of'), '--as-of')
            or timezone.localdate()
        )
        since = (
            _parse_date(options.get('since'), '--since')
            or date(as_of.year, 1, 1)
        )
        if since < date(as_of.year, 1, 1):
            raise CommandError(
                '--since mesti berada dalam tahun yang sama dengan --as-of.'
            )
        if since > as_of:
            raise CommandError('--since tidak boleh selepas --as-of.')

        parameter_values = {
            'p0': options.get('p0') if options.get('p0') is not None else 0.20,
            'p1': options.get('p1') if options.get('p1') is not None else 0.35,
            'h': options.get('h') if options.get('h') is not None else 4.5,
        }
        try:
            parameters = CusumParameters(**parameter_values)
        except (TypeError, ValueError) as exc:
            raise CommandError(str(exc))

        if process_all:
            officers = list(
                User.objects
                .filter(doc_logs__isnull=False)
                .distinct()
                .order_by('username')
            )
        else:
            officer = User.objects.filter(username=username).first()
            if officer is None:
                raise CommandError(
                    f'Username "{username}" tidak dijumpai.'
                )
            officers = [officer]

        summaries = []
        for officer in officers:
            evaluation = _evaluate_officer(
                officer,
                as_of=as_of,
                since=since,
                parameters=parameters,
            )
            result = evaluation.result

            snapshot_created = None
            episode_created_count = 0
            episode_updated_count = 0
            if not options['dry_run']:
                (
                    _,
                    snapshot_created,
                    episode_created_count,
                    episode_updated_count,
                ) = _persist_evaluation(
                    officer=officer,
                    module=module,
                    evaluation=evaluation,
                    parameters=parameters,
                    reset=options['reset'],
                )

            summaries.append({
                'username': officer.username,
                'module': module,
                'as_of': as_of.isoformat(),
                'since': since.isoformat(),
                'observation_count': result.observation_count,
                'skipped_count': evaluation.skipped_count,
                'late_count': evaluation.metadata['late_count'],
                'on_time_count': evaluation.metadata['on_time_count'],
                'pending_count': evaluation.metadata['pending_count'],
                'alert_count': result.alert_count,
                'state': result.state,
                'cusum_value': _round(result.cusum_value),
                'alert_sequences': [
                    alert.sequence for alert in result.alerts
                ],
                'snapshot_written': (
                    None if options['dry_run'] else True
                ),
                'snapshot_created': snapshot_created,
                'episodes_created': episode_created_count,
                'episodes_updated': episode_updated_count,
            })

        if options['json']:
            self.stdout.write(json.dumps({
                'dry_run': bool(options['dry_run']),
                'parameters': {
                    'p0': parameters.p0,
                    'p1': parameters.p1,
                    'h': parameters.h,
                },
                'results': summaries,
            }, ensure_ascii=False))
            return

        if not summaries:
            self.stdout.write(self.style.WARNING(
                'Tiada officer untuk diproses.'
            ))
            return

        mode = 'DRY RUN' if options['dry_run'] else 'RECOMPUTED'
        self.stdout.write(self.style.SUCCESS(
            f'CUSUM {mode} - module={module} '
            f'p0={parameters.p0} p1={parameters.p1} h={parameters.h}'
        ))
        for summary in summaries:
            self.stdout.write(
                f"- {summary['username']}: "
                f"n={summary['observation_count']} "
                f"skipped={summary['skipped_count']} "
                f"late={summary['late_count']} "
                f"on_time={summary['on_time_count']} "
                f"pending={summary['pending_count']} "
                f"alerts={summary['alert_count']} "
                f"state={summary['state']} "
                f"cusum={summary['cusum_value']:.4f}"
            )
            if not options['dry_run']:
                self.stdout.write(
                    f"  snapshot_created={summary['snapshot_created']} "
                    f"episodes_created={summary['episodes_created']} "
                    f"episodes_updated={summary['episodes_updated']}"
                )
