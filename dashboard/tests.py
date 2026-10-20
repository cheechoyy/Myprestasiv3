import shutil
import tempfile
from datetime import date, datetime, timedelta

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    CPDClaim,
    DocumentationLog,
    PolicyDeclaration,
    PolicyItemResponse,
    UserProfile,
)
from .views import (
    MAX_CPD_CERTIFICATE_SIZE,
    POLICY_MULTI_OPTIONS,
    _parse_datetime_value,
)


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


class CPDClaimPhase2Tests(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix='myprestasi-cpd-')
        self.media_override = override_settings(MEDIA_ROOT=self.media_root)
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)
        self.addCleanup(
            lambda: shutil.rmtree(self.media_root, ignore_errors=True)
        )

        self.user = User.objects.create_user(username='cpd_test_user')
        UserProfile.objects.create(
            user=self.user,
            role='STAFF',
            department='CTS',
        )
        self.client.force_login(self.user)

    def _post_cpd(self, **overrides):
        data = {
            'form_type': 'cpd',
            'tajuk': 'Advanced Cardiac Life Support',
            'category': 'A2 - Seminar / Workshop',
            'start_date': '2026-10-10',
            'end_date': '2026-10-12',
            'mata': '12',
        }
        data.update(overrides)
        return self.client.post(
            reverse('staff_dashboard') + '?section=CPD',
            data,
        )

    def _pdf_file(self, name='certificate.pdf'):
        return SimpleUploadedFile(
            name,
            b'%PDF-1.4\n1 0 obj\n<<>>\nendobj\n',
            content_type='application/pdf',
        )

    def test_cpd_persists_end_date_and_certificate(self):
        response = self._post_cpd(proof=self._pdf_file())

        self.assertEqual(response.status_code, 302)
        claim = CPDClaim.objects.get(officer=self.user)
        self.assertEqual(claim.end_date, date(2026, 10, 12))
        self.assertEqual(claim.status, 'PENDING')
        self.assertTrue(claim.certificate.name.startswith('cpd_certificates/'))
        self.assertTrue(
            claim.certificate.storage.exists(claim.certificate.name)
        )

    def test_cpd_accepts_supported_certificate_extensions(self):
        supported_files = [
            ('certificate.pdf', b'%PDF-1.4\n...', 'application/pdf'),
            ('certificate.jpg', b'\xff\xd8\xff\xe0\x00\x10JFIF', 'image/jpeg'),
            ('certificate.jpeg', b'\xff\xd8\xff\xe0\x00\x10JFIF', 'image/jpeg'),
            ('certificate.png', b'\x89PNG\r\n\x1a\n\x00\x00', 'image/png'),
        ]

        for name, content, content_type in supported_files:
            with self.subTest(name=name):
                response = self._post_cpd(
                    proof=SimpleUploadedFile(
                        name,
                        content,
                        content_type=content_type,
                    )
                )
                self.assertEqual(response.status_code, 302)

        claims = CPDClaim.objects.filter(officer=self.user)
        self.assertEqual(claims.count(), len(supported_files))
        for claim in claims:
            self.assertTrue(claim.certificate.name)

    def test_cpd_certificate_is_optional(self):
        response = self._post_cpd(end_date='')

        self.assertEqual(response.status_code, 302)
        claim = CPDClaim.objects.get(officer=self.user)
        self.assertFalse(claim.certificate)
        self.assertIsNone(claim.end_date)

    def test_cpd_allows_end_date_equal_to_start_date(self):
        response = self._post_cpd(end_date='2026-10-10')

        self.assertEqual(response.status_code, 302)
        claim = CPDClaim.objects.get(officer=self.user)
        self.assertEqual(claim.end_date, date(2026, 10, 10))

    def test_cpd_rejects_unsupported_extension(self):
        response = self._post_cpd(
            proof=SimpleUploadedFile(
                'certificate.exe',
                b'MZ\x90\x00',
                content_type='application/octet-stream',
            )
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(CPDClaim.objects.filter(officer=self.user).exists())

    def test_cpd_rejects_renamed_non_pdf_file(self):
        response = self._post_cpd(
            proof=SimpleUploadedFile(
                'certificate.pdf',
                b'MZ\x90\x00',
                content_type='application/pdf',
            )
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(CPDClaim.objects.filter(officer=self.user).exists())

    def test_cpd_rejects_empty_certificate(self):
        response = self._post_cpd(
            proof=SimpleUploadedFile(
                'certificate.pdf',
                b'',
                content_type='application/pdf',
            )
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(CPDClaim.objects.filter(officer=self.user).exists())

    def test_cpd_rejects_oversized_certificate(self):
        oversized_pdf = b'%PDF-1.4\n' + b'0' * MAX_CPD_CERTIFICATE_SIZE
        self.assertGreater(len(oversized_pdf), MAX_CPD_CERTIFICATE_SIZE)

        response = self._post_cpd(
            proof=SimpleUploadedFile(
                'oversized.pdf',
                oversized_pdf,
                content_type='application/pdf',
            )
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(CPDClaim.objects.filter(officer=self.user).exists())

    def test_cpd_rejects_end_date_before_start_date(self):
        response = self._post_cpd(end_date='2026-10-09')

        self.assertEqual(response.status_code, 302)
        self.assertFalse(CPDClaim.objects.filter(officer=self.user).exists())

    def test_cpd_rejects_invalid_end_date(self):
        response = self._post_cpd(end_date='not-a-date')

        self.assertEqual(response.status_code, 302)
        self.assertFalse(CPDClaim.objects.filter(officer=self.user).exists())


class PolicyDeclarationPhase2Tests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='policy_test_user')
        UserProfile.objects.create(
            user=self.user,
            role='STAFF',
            department='CTS',
        )
        self.client.force_login(self.user)

    def _policy_data(self):
        data = {
            'form_type': 'policy',
            'declaration_confirm': 'Yes',
            'q1': list(POLICY_MULTI_OPTIONS[1]),
            'q2': list(POLICY_MULTI_OPTIONS[2]),
            'q3': list(POLICY_MULTI_OPTIONS[3]),
        }
        data.update({f'q{q}': 'Yes' for q in range(4, 21)})
        return data

    def _post_policy(self, **overrides):
        data = self._policy_data()
        data.update(overrides)
        return self.client.post(
            reverse('staff_dashboard') + '?section=Clinical',
            data,
        )

    def test_policy_persists_item_metrics_and_domain_scores(self):
        response = self._post_policy(
            q2=list(POLICY_MULTI_OPTIONS[2])[:3],
            q8='No',
        )

        self.assertEqual(response.status_code, 302)
        declaration = PolicyDeclaration.objects.get(officer=self.user)
        self.assertEqual(declaration.responses.count(), 20)

        response_map = {
            item.question_number: item
            for item in declaration.responses.all()
        }

        self.assertEqual(response_map[1].selected_count, 6)
        self.assertEqual(response_map[1].max_count, 6)
        self.assertAlmostEqual(response_map[1].compliance_ratio, 1.0)
        self.assertTrue(response_map[1].is_compliant)

        self.assertEqual(response_map[2].selected_count, 3)
        self.assertEqual(response_map[2].max_count, 4)
        self.assertAlmostEqual(response_map[2].compliance_ratio, 0.75)
        self.assertFalse(response_map[2].is_compliant)

        self.assertEqual(response_map[8].selected_count, 0)
        self.assertEqual(response_map[8].max_count, 1)
        self.assertAlmostEqual(response_map[8].compliance_ratio, 0.0)
        self.assertFalse(response_map[8].is_compliant)

        self.assertAlmostEqual(declaration.score_domain_a, 87.5)
        self.assertAlmostEqual(declaration.score_domain_b, 100.0)
        self.assertAlmostEqual(declaration.score_domain_c, 66.7)
        self.assertAlmostEqual(declaration.score_domain_d, 100.0)
        self.assertAlmostEqual(declaration.score_domain_e, 100.0)

    def test_policy_allows_empty_multi_select_with_zero_metrics(self):
        response = self._post_policy(q1=[], q2=[], q3=[])

        self.assertEqual(response.status_code, 302)
        declaration = PolicyDeclaration.objects.get(officer=self.user)
        response_map = {
            item.question_number: item
            for item in declaration.responses.all()
        }

        for question_number, max_count in ((1, 6), (2, 4), (3, 4)):
            item = response_map[question_number]
            self.assertEqual(item.selected_count, 0)
            self.assertEqual(item.max_count, max_count)
            self.assertAlmostEqual(item.compliance_ratio, 0.0)
            self.assertFalse(item.is_compliant)

        self.assertAlmostEqual(declaration.score_domain_a, 0.0)
        self.assertAlmostEqual(declaration.score_domain_b, 80.0)
        self.assertAlmostEqual(declaration.score_domain_c, 100.0)
        self.assertAlmostEqual(declaration.score_domain_d, 100.0)
        self.assertAlmostEqual(declaration.score_domain_e, 100.0)

    def test_policy_ignores_duplicate_and_unknown_multi_select_values(self):
        response = self._post_policy(
            q1=['ascvd', 'ascvd', 'visual', 'unknown-option'],
        )

        self.assertEqual(response.status_code, 302)
        declaration = PolicyDeclaration.objects.get(officer=self.user)
        q1 = declaration.responses.get(question_number=1)

        self.assertEqual(q1.selected_count, 2)
        self.assertEqual(q1.max_count, 6)
        self.assertAlmostEqual(q1.compliance_ratio, 2 / 6, places=6)
        self.assertFalse(q1.is_compliant)

    def test_policy_rejects_incomplete_radio_answers(self):
        response = self._post_policy(q20='')

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            PolicyDeclaration.objects.filter(officer=self.user).exists()
        )
        self.assertEqual(PolicyItemResponse.objects.count(), 0)

    def test_policy_rejects_missing_final_declaration(self):
        response = self._post_policy(declaration_confirm='')

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            PolicyDeclaration.objects.filter(officer=self.user).exists()
        )
        self.assertEqual(PolicyItemResponse.objects.count(), 0)
