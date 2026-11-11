import random
from datetime import datetime, time, timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from dashboard.models import (
    CPDClaim,
    DocumentationLog,
    ProductivityLog,
    UserProfile,
)
from dashboard.services import get_module_scores


SIM_DOC_PREFIX = 'SIM-DOC-'
SIM_PROD_PREFIX = 'SIM-PROD-'
SIM_CPD_PREFIX = '[SIM]'


def _build_documentation_rows(rng, start_date, days):
    rows = []
    for offset in range(days):
        day = start_date + timedelta(days=offset)
        notes_today = rng.randint(1, 3) if day.weekday() < 5 else 1
        for index in range(1, notes_today + 1):
            roll = rng.random()
            if roll < 0.85:
                is_completed = True
                is_ontime = True
                delay_hours = round(rng.uniform(0.25, 3.75), 2)
                reason = None
                category = None
            elif roll < 0.95:
                is_completed = True
                is_ontime = False
                delay_hours = round(rng.uniform(4.5, 12.0), 2)
                category = rng.choice([
                    'High patient load',
                    'Emergency case',
                    'System/Network down',
                ])
                reason = category
            else:
                is_completed = False
                is_ontime = True
                delay_hours = 0.0
                reason = None
                category = None

            encounter_at = timezone.make_aware(datetime.combine(
                day,
                time(rng.randint(8, 16), rng.choice([0, 15, 30, 45])),
            ))
            completed_at = (
                encounter_at + timedelta(hours=delay_hours)
                if is_completed else None
            )
            rows.append({
                'patient_id': f'{SIM_DOC_PREFIX}{day:%Y%m%d}-{index:02d}',
                'encounter_type': 'Simulation Clinic Note',
                'encounter_at': encounter_at,
                'completed_at': completed_at,
                'delay_hours': delay_hours,
                'reason_for_delay': reason,
                'delay_reason_category': category,
                'delay_reason_remarks': reason,
                'is_ontime': is_ontime,
                'is_completed': is_completed,
            })
    return rows


def _build_productivity_rows(rng, start_date, days):
    rows = []
    for offset in range(days):
        day = start_date + timedelta(days=offset)
        patients_cleared = max(
            15,
            min(65, int(round(rng.gauss(38.22, 6.0)))),
        )
        punctuality_rate = round(rng.uniform(90.0, 99.0), 1)
        rows.append({
            'record_date': day,
            'patients_cleared': patients_cleared,
            'ward_cases': rng.randint(0, 5),
            'procedures_completed': rng.randint(0, 3),
            'avg_consult_minutes': round(rng.uniform(10.0, 16.0), 1),
            'punctuality_rate': punctuality_rate,
            'punctuality_status': (
                'On Time' if punctuality_rate >= 95.0 else 'Monitor'
            ),
            'operational_notes': f'{SIM_PROD_PREFIX}{day.isoformat()}',
        })
    return rows


def _build_cpd_rows(days, today):
    specs = (
        (20, 'APPROVED', 'CME Workshop', 0.85),
        (15, 'APPROVED', 'Workshop', 0.60),
        (10, 'APPROVED', 'Online Quiz', 0.35),
        (10, 'PENDING', 'CME', 0.20),
        (5, 'REJECTED', 'Conference', 0.10),
    )
    topics = (
        'National Diabetes Management Protocol Update',
        'Advanced Insulin Titration Workshop',
        'Medical Ethics and Clinical Governance',
        'Cardiovascular Risk Stratification',
        'Primary Care Quality Improvement Forum',
    )
    rows = []
    for index, (points, status, category, fraction) in enumerate(specs):
        days_ago = max(
            1,
            min(days - 1, int(round(days * fraction))),
        )
        event_date = today - timedelta(days=days_ago)
        rows.append({
            'category': category,
            'sub_category': 'Simulation',
            'role': 'ATTENDEE',
            'topic': f'{SIM_CPD_PREFIX} {topics[index]}',
            'points': points,
            'event_date': event_date,
            'end_date': None,
            'status': status,
            'verified_at': (
                timezone.now() if status == 'APPROVED' else None
            ),
            'rejection_reason': (
                'Simulation record rejected.' if status == 'REJECTED' else None
            ),
        })
    return rows


def _simulation_counts(user):
    if user is None:
        return 0, 0, 0
    return (
        DocumentationLog.objects.filter(
            officer=user,
            patient_id__startswith=SIM_DOC_PREFIX,
        ).count(),
        ProductivityLog.objects.filter(
            officer=user,
            operational_notes__startswith=SIM_PROD_PREFIX,
        ).count(),
        CPDClaim.objects.filter(
            officer=user,
            topic__startswith=SIM_CPD_PREFIX,
        ).count(),
    )


class Command(BaseCommand):
    help = (
        'Jana data simulasi 30 hari untuk Documentation, Productivity dan CPD '
        'bagi akaun sim_doctor tanpa menyentuh data pengguna sebenar.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--username',
            type=str,
            default='sim_doctor',
            help='Username simulation (default: sim_doctor)',
        )
        parser.add_argument(
            '--days',
            type=int,
            default=30,
            help='Bilangan hari data simulasi (default: 30)',
        )
        parser.add_argument(
            '--seed',
            type=int,
            default=42,
            help='Seed random untuk data deterministik (default: 42)',
        )
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Padam rekod simulasi sedia ada sebelum menjana semula',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Papar ringkasan tanpa menulis ke database',
        )
        parser.add_argument(
            '--password',
            type=str,
            default=None,
            help='Password untuk akaun simulation baru atau sedia ada',
        )
        parser.add_argument(
            '--department',
            type=str,
            default='FMS',
            help='Kod jabatan UserProfile (default: FMS)',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Benarkan command berjalan walaupun DEBUG=False',
        )

    def handle(self, *args, **options):
        username = (options.get('username') or '').strip()
        if not username:
            raise CommandError('--username tidak boleh kosong.')

        days = int(options['days'])
        if days < 7 or days > 365:
            raise CommandError('--days mesti antara 7 dan 365.')

        department = (options.get('department') or 'FMS').strip().upper()
        valid_departments = {
            choice[0] for choice in UserProfile.DEPT_CHOICES
        }
        if department not in valid_departments:
            raise CommandError(
                f'--department mesti salah satu daripada: '
                f'{", ".join(sorted(valid_departments))}.'
            )

        if not settings.DEBUG and not options['force']:
            raise CommandError(
                'Command ini enggan berjalan apabila DEBUG=False. '
                'Gunakan --force hanya jika anda benar-benar pasti.'
            )

        today = timezone.localdate()
        start_date = today - timedelta(days=days - 1)
        rng = random.Random(options['seed'])

        documentation_rows = _build_documentation_rows(
            rng, start_date, days
        )
        productivity_rows = _build_productivity_rows(
            rng, start_date, days
        )
        cpd_rows = _build_cpd_rows(days, today)

        existing_user = User.objects.filter(username=username).first()
        existing_counts = _simulation_counts(existing_user)

        if any(existing_counts) and not options['reset']:
            message = (
                'Rekod simulasi sedia ada dijumpai. '
                'Gunakan --reset untuk menggantikannya.'
            )
            if options['dry_run']:
                self.stdout.write(self.style.WARNING(message))
            else:
                raise CommandError(message)

        if options['dry_run']:
            self._print_summary(
                username=username,
                days=days,
                start_date=start_date,
                today=today,
                documentation_rows=documentation_rows,
                productivity_rows=productivity_rows,
                cpd_rows=cpd_rows,
                action='DRY RUN',
                existing_counts=existing_counts,
            )
            return


        deleted_counts = (0, 0, 0)
        with transaction.atomic():
            user, created = User.objects.get_or_create(username=username)
            if created:
                user.first_name = 'Simulation'
                user.last_name = 'Doctor'
                user.email = f'{username}@simulation.local'
                if options['password']:
                    user.set_password(options['password'])
                else:
                    user.set_unusable_password()
                user.save()
            elif options['password']:
                user.set_password(options['password'])
                user.save(update_fields=['password'])

            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.role = 'STAFF'
            profile.department = department
            profile.assigned_facility = 'Simulation Clinic'
            profile.assigned_state = 'Selangor'
            profile.phone_number = '+60000000000'
            profile.save()

            if options['reset']:
                deleted_counts = (
                    DocumentationLog.objects.filter(
                        officer=user,
                        patient_id__startswith=SIM_DOC_PREFIX,
                    ).delete()[0],
                    ProductivityLog.objects.filter(
                        officer=user,
                        operational_notes__startswith=SIM_PROD_PREFIX,
                    ).delete()[0],
                    CPDClaim.objects.filter(
                        officer=user,
                        topic__startswith=SIM_CPD_PREFIX,
                    ).delete()[0],
                )

            for row in documentation_rows:
                DocumentationLog.objects.create(officer=user, **row)
            for row in productivity_rows:
                ProductivityLog.objects.create(officer=user, **row)
            for row in cpd_rows:
                CPDClaim.objects.create(officer=user, **row)

        self._print_summary(
            username=username,
            days=days,
            start_date=start_date,
            today=today,
            documentation_rows=documentation_rows,
            productivity_rows=productivity_rows,
            cpd_rows=cpd_rows,
            action='SEEDED',
            existing_counts=existing_counts,
            deleted_counts=deleted_counts,
        )
        if not created:
            self.stdout.write(
                'Akaun sedia ada digunakan; password tidak diubah '
                'kecuali --password diberikan.'
            )
        self._print_scores(user, today)


    def _print_summary(
        self,
        username,
        days,
        start_date,
        today,
        documentation_rows,
        productivity_rows,
        cpd_rows,
        action,
        existing_counts=(0, 0, 0),
        deleted_counts=(0, 0, 0),
    ):
        on_time = sum(
            1 for row in documentation_rows
            if row['is_completed'] and row['is_ontime']
        )
        late = sum(
            1 for row in documentation_rows
            if row['is_completed'] and not row['is_ontime']
        )
        pending = sum(
            1 for row in documentation_rows
            if not row['is_completed']
        )
        average_patients = (
            sum(row['patients_cleared'] for row in productivity_rows)
            / len(productivity_rows)
            if productivity_rows else 0
        )
        approved_points = sum(
            row['points'] for row in cpd_rows
            if row['status'] == 'APPROVED'
        )
        pending_points = sum(
            row['points'] for row in cpd_rows
            if row['status'] == 'PENDING'
        )
        rejected_points = sum(
            row['points'] for row in cpd_rows
            if row['status'] == 'REJECTED'
        )

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'[{action}] Simulation data: {username}'
        ))
        self.stdout.write(
            f'Julat: {start_date.isoformat()} hingga {today.isoformat()} '
            f'({days} hari)'
        )
        self.stdout.write(
            f'Documentation: {len(documentation_rows)} nota '
            f'({on_time} on-time, {late} lewat, {pending} pending)'
        )
        self.stdout.write(
            f'Productivity: {len(productivity_rows)} log; '
            f'purata pesakit {average_patients:.1f}'
        )
        self.stdout.write(
            f'CPD: {len(cpd_rows)} claim; '
            f'{approved_points} mata approved, '
            f'{pending_points} pending, {rejected_points} rejected'
        )
        if any(existing_counts):
            self.stdout.write(
                'Rekod simulasi sedia ada: '
                f'docs={existing_counts[0]}, '
                f'productivity={existing_counts[1]}, '
                f'cpd={existing_counts[2]}'
            )
        if any(deleted_counts):
            self.stdout.write(
                'Dipadam: '
                f'docs={deleted_counts[0]}, '
                f'productivity={deleted_counts[1]}, '
                f'cpd={deleted_counts[2]}'
            )

    def _print_scores(self, user, as_of):
        scores = get_module_scores(user, as_of=as_of)
        self.stdout.write(self.style.MIGRATE_HEADING('Module scores'))
        for module in scores['modules']:
            self.stdout.write(
                f"  {module['label']}: "
                f"{module['display']} ({module['status']})"
            )

