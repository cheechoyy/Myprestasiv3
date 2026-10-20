from datetime import datetime, timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import DocumentationLog, UserProfile
from .views import _parse_datetime_value


class ParseDatetimeValueTests(TestCase):
    def test_naive_datetime_is_made_timezone_aware(self):
        parsed = _parse_datetime_value('2026-10-20T09:30')

        self.assertTrue(timezone.is_aware(parsed))
        self.assertEqual(parsed.utcoffset(), timedelta(hours=8))
        self.assertEqual(parsed.year, 2026)
        self.assertEqual(parsed.hour, 9)
        self.assertEqual(parsed.minute, 30)

    def test_invalid_datetime_returns_default(self):
        self.assertIsNone(_parse_datetime_value('not-a-datetime'))


class DocumentationLogPhase2Tests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='doc_test_user')
        UserProfile.objects.create(
            user=self.user,
            role='STAFF',
            department='CTS',
        )
        self.client.force_login(self.user)

    def test_documentation_persists_phase1_fields(self):
        response = self.client.post(
            reverse('staff_dashboard') + '?section=Documentation',
            {
                'form_type': 'doc',
                'patient_id': 'RN123456',
                'encounter_type': 'Outpatient Consultation',
                'encounter_at': '2026-10-20T09:00',
                'completed_at': '2026-10-20T11:30',
                'reason': 'System/Network down',
                'reason_remarks': 'Server downtime',
            },
        )

        self.assertEqual(response.status_code, 302)
        log = DocumentationLog.objects.get(officer=self.user)
        self.assertEqual(log.encounter_type, 'Outpatient Consultation')
        self.assertEqual(log.encounter_at, datetime(2026, 10, 20, 9, 0, tzinfo=timezone.get_default_timezone()))
        self.assertEqual(log.completed_at, datetime(2026, 10, 20, 11, 30, tzinfo=timezone.get_default_timezone()))
        self.assertTrue(timezone.is_aware(log.encounter_at))
        self.assertTrue(timezone.is_aware(log.completed_at))
        self.assertEqual(log.delay_hours, 2.5)
        self.assertEqual(log.delay_reason_category, 'System/Network down')
        self.assertEqual(log.delay_reason_remarks, 'Server downtime')
        self.assertEqual(
            log.reason_for_delay,
            'System/Network down — Server downtime',
        )
        self.assertTrue(log.is_ontime)
        self.assertTrue(log.patient_id.startswith('REF-'))

    def test_documentation_uses_null_for_optional_fields(self):
        response = self.client.post(
            reverse('staff_dashboard') + '?section=Documentation',
            {
                'form_type': 'doc',
                'patient_id': 'RN654321',
                'encounter_at': '2026-10-21T08:00',
                'completed_at': '2026-10-21T08:30',
                'encounter_type': '',
                'reason': '',
                'reason_remarks': '',
            },
        )

        self.assertEqual(response.status_code, 302)
        log = DocumentationLog.objects.get(officer=self.user)
        self.assertIsNone(log.encounter_type)
        self.assertIsNone(log.delay_reason_category)
        self.assertIsNone(log.delay_reason_remarks)
        self.assertIsNone(log.reason_for_delay)
        self.assertTrue(log.is_ontime)
