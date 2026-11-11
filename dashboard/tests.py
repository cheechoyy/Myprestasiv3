import shutil
import tempfile
from datetime import date, datetime, timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    CPDClaim,
    DocumentationLog,
    EmotionalReflection,
    HODFeedback,
    InnovationProject,
    MentorshipSession,
    MyPSQFeedback,
    PeerKudos,
    PolicyDeclaration,
    PolicyItemResponse,
    ProcedureLog,
    ProductivityLog,
    UserProfile,
)
from .services import calculate_pri_score, get_module_scores
from .views import (
    MAX_CPD_CERTIFICATE_SIZE,
    POLICY_MULTI_OPTIONS,
    _average_reflection_domains,
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


class ClinicalProcedurePhase2Tests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='clinical_test_user')
        UserProfile.objects.create(
            user=self.user,
            role='STAFF',
            department='CTS',
        )
        self.client.force_login(self.user)

    def _cts_data(self):
        return {
            'form_type': 'skills_cts',
            'facility': 'Heart Center IJN',
            'procedure_date': '2026-10-20',
            'procedure_name': 'CABG (Coronary Artery Bypass Graft)',
            'score': '88',
            'primary_diagnosis': 'Coronary Artery Disease (CAD)',
            'previous_intervention': 'Previous PCI / Stenting',
            'comorbidities': [
                'Diabetes (on Insulin)',
                'Hypertension',
                'Active Smoker',
            ],
            'lvef_percent': '45',
            'coronary_anatomy': 'Triple Vessel Disease (TVD)',
            'nyha_class': 'III',
        }

    def _fms_data(self):
        return {
            'form_type': 'skills_fms',
            'facility': 'Klinik Kesihatan Medong',
            'procedure_date': '2026-10-21',
            'procedure_name': 'Type 2 Diabetes Mellitus (T2DM)',
            'duration_illness': '> 5 Years',
            'comorbidities': [
                'Active Smoker',
                'Obesity (BMI > 27.5)',
            ],
            'bp': ' 135 / 85 ',
            'hba1c_percent': '7.2',
            'ldl_mmol_l': '2.6',
            'score': '82',
            'target': 'Yes',
        }

    def _post_cts(self, **overrides):
        data = self._cts_data()
        data.update(overrides)
        return self.client.post(
            reverse('staff_dashboard') + '?section=Clinical',
            data,
        )

    def _post_fms(self, **overrides):
        data = self._fms_data()
        data.update(overrides)
        return self.client.post(
            reverse('staff_dashboard') + '?section=Clinical',
            data,
        )

    def test_cts_persists_all_phase1_procedure_fields(self):
        response = self._post_cts()

        self.assertEqual(response.status_code, 302)
        log = ProcedureLog.objects.get(officer=self.user)
        self.assertEqual(log.clinical_domain, 'CTS')
        self.assertEqual(log.facility, 'Heart Center IJN')
        self.assertEqual(
            log.procedure_name,
            'CABG (Coronary Artery Bypass Graft)',
        )
        self.assertEqual(log.procedure_date, date(2026, 10, 20))
        self.assertEqual(
            log.primary_diagnosis,
            'Coronary Artery Disease (CAD)',
        )
        self.assertEqual(
            log.previous_intervention,
            'Previous PCI / Stenting',
        )
        self.assertEqual(
            log.comorbidities,
            ['Diabetes (on Insulin)', 'Hypertension', 'Active Smoker'],
        )
        self.assertEqual(log.lvef_percent, 45.0)
        self.assertEqual(
            log.coronary_anatomy,
            'Triple Vessel Disease (TVD)',
        )
        self.assertEqual(log.nyha_class, 'III')
        self.assertEqual(log.score, 88.0)
        self.assertTrue(log.is_successful)
        self.assertFalse(log.complication_occurred)
        self.assertIsNone(log.target_achieved)
        self.assertIsNone(log.duration_illness)
        self.assertIsNone(log.systolic_bp)
        self.assertIsNone(log.diastolic_bp)
        self.assertIsNone(log.hba1c_percent)
        self.assertIsNone(log.ldl_mmol_l)
        self.assertIsNone(log.patient_ref)

    def test_cts_target_achieved_remains_none_and_success_is_true(self):
        response = self._post_cts()

        self.assertEqual(response.status_code, 302)
        log = ProcedureLog.objects.get(officer=self.user)
        self.assertIsNone(log.target_achieved)
        self.assertTrue(log.is_successful)

    def test_fms_persists_vitals_labs_and_clinical_outcome(self):
        response = self._post_fms()

        self.assertEqual(response.status_code, 302)
        log = ProcedureLog.objects.get(officer=self.user)
        self.assertEqual(log.clinical_domain, 'FMS')
        self.assertEqual(log.facility, 'Klinik Kesihatan Medong')
        self.assertEqual(
            log.procedure_name,
            'Type 2 Diabetes Mellitus (T2DM)',
        )
        self.assertEqual(log.procedure_date, date(2026, 10, 21))
        self.assertEqual(log.duration_illness, '> 5 Years')
        self.assertEqual(
            log.comorbidities,
            ['Active Smoker', 'Obesity (BMI > 27.5)'],
        )
        self.assertEqual(log.systolic_bp, 135)
        self.assertEqual(log.diastolic_bp, 85)
        self.assertEqual(log.hba1c_percent, 7.2)
        self.assertEqual(log.ldl_mmol_l, 2.6)
        self.assertEqual(log.score, 82.0)
        self.assertTrue(log.target_achieved)
        self.assertTrue(log.is_successful)
        self.assertFalse(log.complication_occurred)
        self.assertIsNone(log.patient_ref)
        self.assertIsNone(log.primary_diagnosis)
        self.assertIsNone(log.previous_intervention)
        self.assertIsNone(log.lvef_percent)
        self.assertIsNone(log.coronary_anatomy)
        self.assertIsNone(log.nyha_class)

    def test_fms_target_not_achieved_keeps_procedure_successful(self):
        response = self._post_fms(target='No')

        self.assertEqual(response.status_code, 302)
        log = ProcedureLog.objects.get(officer=self.user)
        self.assertFalse(log.target_achieved)
        self.assertTrue(log.is_successful)
        self.assertFalse(log.complication_occurred)

    def test_fms_rejects_malformed_or_out_of_range_bp(self):
        invalid_values = (
            '135',
            '135/',
            'abc/85',
            '85/135',
            '400/85',
            '135/20',
        )

        for bp in invalid_values:
            with self.subTest(bp=bp):
                response = self._post_fms(bp=bp)
                self.assertEqual(response.status_code, 302)
                self.assertFalse(
                    ProcedureLog.objects.filter(officer=self.user).exists()
                )

    def test_comorbidities_are_whitelisted_with_none_as_empty(self):
        self._post_cts(
            comorbidities=[
                'None',
                'Hypertension',
                'Hypertension',
                'unknown-option',
                'Active Smoker',
            ],
        )
        cts_log = ProcedureLog.objects.get(
            officer=self.user,
            clinical_domain='CTS',
        )
        self.assertEqual(
            cts_log.comorbidities,
            ['Hypertension', 'Active Smoker'],
        )

        self._post_fms(comorbidities=['None'])
        fms_log = ProcedureLog.objects.get(
            officer=self.user,
            clinical_domain='FMS',
        )
        self.assertEqual(fms_log.comorbidities, [])

    def test_fms_blank_optional_fields_become_null_or_empty(self):
        response = self._post_fms(
            duration_illness='',
            hba1c_percent='',
            ldl_mmol_l='',
            comorbidities=[],
        )

        self.assertEqual(response.status_code, 302)
        log = ProcedureLog.objects.get(officer=self.user)
        self.assertIsNone(log.duration_illness)
        self.assertIsNone(log.hba1c_percent)
        self.assertIsNone(log.ldl_mmol_l)
        self.assertEqual(log.comorbidities, [])

    def test_patient_ref_remains_null_for_cts_and_fms(self):
        self._post_cts()
        self._post_fms()

        self.assertEqual(ProcedureLog.objects.count(), 2)
        self.assertEqual(
            ProcedureLog.objects.filter(
                officer=self.user,
                patient_ref__isnull=True,
            ).count(),
            2,
        )

class OperationalPersistencePhase2Tests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='operational_test_user')
        UserProfile.objects.create(
            user=self.user,
            role='STAFF',
            department='CTS',
        )
        self.client.force_login(self.user)

    def _prod_data(self):
        return {
            'form_type': 'ops_prod',
            'prod_date': '2026-10-19',
            'outpatient_count': '42',
            'ward_count': '9',
            'procedure_count': '3',
            'avg_consult': '14.5',
            'punctuality': 'Slight Delay',
            'prod_notes': 'Klinik dilewatkan oleh kes kecemasan.',
        }

    def _post_prod(self, **overrides):
        data = self._prod_data()
        data.update(overrides)
        return self.client.post(
            reverse('staff_dashboard') + '?section=Operational',
            data,
        )

    def _innovation_data(self, innovation_type, **overrides):
        data = {
            'form_type': 'ops_innov',
            'innovation_type': innovation_type,
            'category': innovation_type,
        }
        data.update(overrides)
        return data

    def _post_innovation(self, data):
        return self.client.post(
            reverse('staff_dashboard') + '?section=Operational',
            data,
        )

    def test_productivity_persists_all_fields(self):
        response = self._post_prod()

        self.assertEqual(response.status_code, 302)
        log = ProductivityLog.objects.get(officer=self.user)
        self.assertEqual(log.record_date, date(2026, 10, 19))
        self.assertEqual(log.patients_cleared, 42)
        self.assertEqual(log.ward_cases, 9)
        self.assertEqual(log.procedures_completed, 3)
        self.assertEqual(log.avg_consult_minutes, 14.5)
        self.assertEqual(log.punctuality_status, 'Slight Delay')
        self.assertEqual(log.punctuality_rate, 85.0)
        self.assertEqual(
            log.operational_notes,
            'Klinik dilewatkan oleh kes kecemasan.',
        )

    def test_productivity_punctuality_mapping(self):
        expected_rates = {
            'On-Time': 100.0,
            'Slight Delay': 85.0,
            'Ward Round Extension': 70.0,
        }

        for status, expected_rate in expected_rates.items():
            with self.subTest(status=status):
                ProductivityLog.objects.all().delete()
                response = self._post_prod(punctuality=status)

                self.assertEqual(response.status_code, 302)
                log = ProductivityLog.objects.get(officer=self.user)
                self.assertEqual(log.punctuality_status, status)
                self.assertEqual(log.punctuality_rate, expected_rate)

    def test_productivity_rejects_invalid_or_missing_date(self):
        for invalid_date in ('', '2026-13-01', '20-10-2026'):
            with self.subTest(prod_date=invalid_date):
                response = self._post_prod(prod_date=invalid_date)

                self.assertEqual(response.status_code, 302)
                self.assertFalse(
                    ProductivityLog.objects.filter(officer=self.user).exists()
                )

        data = self._prod_data()
        data.pop('prod_date')
        response = self.client.post(
            reverse('staff_dashboard') + '?section=Operational',
            data,
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            ProductivityLog.objects.filter(officer=self.user).exists()
        )

    def test_productivity_rejects_invalid_punctuality(self):
        for invalid_status in ('', 'Late'):
            with self.subTest(punctuality=invalid_status):
                response = self._post_prod(punctuality=invalid_status)

                self.assertEqual(response.status_code, 302)
                self.assertFalse(
                    ProductivityLog.objects.filter(officer=self.user).exists()
                )

    def test_productivity_blank_optional_fields(self):
        response = self._post_prod(
            ward_count='',
            procedure_count='',
            avg_consult='',
            prod_notes='',
        )

        self.assertEqual(response.status_code, 302)
        log = ProductivityLog.objects.get(officer=self.user)
        self.assertIsNone(log.ward_cases)
        self.assertIsNone(log.procedures_completed)
        self.assertEqual(log.avg_consult_minutes, 12.0)
        self.assertIsNone(log.operational_notes)


    def test_research_persists_structured_fields(self):
        response = self._post_innovation(
            self._innovation_data(
                'Research',
                title='Prevalence of T2DM in Sarawak',
                nmrr_id='NMRR-25-123',
                res_role='Co-Investigator',
            )
        )

        self.assertEqual(response.status_code, 302)
        project = InnovationProject.objects.get(officer=self.user)
        self.assertEqual(project.project_title, 'Prevalence of T2DM in Sarawak')
        self.assertEqual(project.innovation_type, 'Research')
        self.assertEqual(project.category, 'Research')
        self.assertEqual(project.nmrr_id, 'NMRR-25-123')
        self.assertEqual(project.research_role, 'Co-Investigator')
        self.assertIn('Role: Co-Investigator', project.description)
        self.assertIn('NMRR: NMRR-25-123', project.description)
        self.assertNotIn('Technology:', project.description)
        self.assertNotIn('Details:', project.description)

    def test_clinical_trial_persists_structured_fields(self):
        response = self._post_innovation(
            self._innovation_data(
                'Clinical Trial',
                title='CRC-26-001A',
                phase='Phase III',
                recruitment_count='12',
                trial_stat='Closed',
            )
        )

        self.assertEqual(response.status_code, 302)
        project = InnovationProject.objects.get(officer=self.user)
        self.assertEqual(project.project_title, 'CRC-26-001A')
        self.assertEqual(project.innovation_type, 'Clinical Trial')
        self.assertEqual(project.category, 'Clinical Trial')
        self.assertEqual(project.trial_phase, 'Phase III')
        self.assertEqual(project.recruitment_count, 12)
        self.assertEqual(project.trial_status, 'Closed')
        self.assertIn('Trial Phase: Phase III', project.description)
        self.assertIn('Recruitment: 12', project.description)
        self.assertIn('Trial Status: Closed', project.description)

    def test_technology_adoption_persists_structured_fields(self):
        response = self._post_innovation(
            self._innovation_data(
                'Technology Adoption',
                title='PACS Imaging System',
                adoption_date='2026-10-18',
                tech_lvl='Expert (Trainer)',
                technology_name='Ghost Legacy Value',
            )
        )

        self.assertEqual(response.status_code, 302)
        project = InnovationProject.objects.get(officer=self.user)
        self.assertEqual(project.project_title, 'PACS Imaging System')
        self.assertEqual(project.innovation_type, 'Technology Adoption')
        self.assertEqual(project.category, 'Technology Adoption')
        self.assertEqual(project.adoption_date, date(2026, 10, 18))
        self.assertEqual(project.proficiency_level, 'Expert (Trainer)')
        self.assertIn('Adoption Date: 2026-10-18', project.description)
        self.assertIn('Proficiency: Expert (Trainer)', project.description)
        self.assertNotIn('Ghost Legacy Value', project.description)


    def test_publication_persists_structured_fields_and_title_fallback(self):
        response = self._post_innovation(
            self._innovation_data(
                'Publication',
                journal='MedJ Malaysia',
                doi='10.1234/medj.2026.001',
                title='',
                year='2025',
            )
        )

        self.assertEqual(response.status_code, 302)
        project = InnovationProject.objects.get(officer=self.user)
        self.assertEqual(project.project_title, 'MedJ Malaysia')
        self.assertEqual(project.innovation_type, 'Publication')
        self.assertEqual(project.category, 'Publication')
        self.assertEqual(project.journal_name, 'MedJ Malaysia')
        self.assertEqual(project.doi, '10.1234/medj.2026.001')
        self.assertEqual(project.publication_year, 2025)
        self.assertIn('Journal: MedJ Malaysia', project.description)
        self.assertIn('DOI: 10.1234/medj.2026.001', project.description)
        self.assertIn('Year: 2025', project.description)

    def test_invalid_adoption_date_or_recruitment_count_is_rejected(self):
        response = self._post_innovation(
            self._innovation_data(
                'Technology Adoption',
                title='PACS Imaging System',
                adoption_date='2026-02-30',
            )
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            InnovationProject.objects.filter(officer=self.user).exists()
        )

        response = self._post_innovation(
            self._innovation_data(
                'Clinical Trial',
                title='CRC-26-001A',
                recruitment_count='-5',
            )
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            InnovationProject.objects.filter(officer=self.user).exists()
        )

    def test_optional_innovation_fields_become_null(self):
        self._post_innovation(
            self._innovation_data(
                'Research',
                title='Research Without Optional Fields',
                nmrr_id='',
                res_role='',
            )
        )
        self._post_innovation(
            self._innovation_data(
                'Clinical Trial',
                title='CRC-26-002A',
                phase='',
                recruitment_count='',
                trial_stat='',
            )
        )
        self._post_innovation(
            self._innovation_data(
                'Technology Adoption',
                title='Technology Without Optional Fields',
                adoption_date='',
                tech_lvl='',
            )
        )
        self._post_innovation(
            self._innovation_data(
                'Publication',
                journal='Journal Without Year',
                doi='10.1234/no-year',
                title='',
                year='',
            )
        )

        research = InnovationProject.objects.get(innovation_type='Research')
        self.assertIsNone(research.nmrr_id)
        self.assertIsNone(research.research_role)

        trial = InnovationProject.objects.get(innovation_type='Clinical Trial')
        self.assertIsNone(trial.trial_phase)
        self.assertIsNone(trial.recruitment_count)
        self.assertIsNone(trial.trial_status)

        technology = InnovationProject.objects.get(
            innovation_type='Technology Adoption'
        )
        self.assertIsNone(technology.adoption_date)
        self.assertIsNone(technology.proficiency_level)

        publication = InnovationProject.objects.get(innovation_type='Publication')
        self.assertEqual(publication.project_title, 'Journal Without Year')
        self.assertIsNone(publication.publication_year)


class OperationalDashboardPhase3BTests(TestCase):
    def setUp(self):
        # Django 5.0.3 copies the template context in the test client, which
        # fails under Python 3.14. Keep the rendered context object as-is so
        # response.context remains available for these assertions.
        copy_patcher = patch('django.test.client.copy', new=lambda value: value)
        copy_patcher.start()
        self.addCleanup(copy_patcher.stop)

        self.year = timezone.localdate().year
        self.user = self._staff(
            'ops_dashboard_user',
            first_name='Amir',
            last_name='Hakim',
        )
        self.client.force_login(self.user)
        self.url = reverse('staff_dashboard') + '?section=Operational'

    def _staff(self, username, department='CTS', first_name='', last_name=''):
        user = User.objects.create_user(
            username=username,
            first_name=first_name,
            last_name=last_name,
        )
        UserProfile.objects.create(
            user=user,
            role='STAFF',
            department=department,
        )
        return user

    def _project(
        self,
        user,
        title,
        innovation_type='Research',
        category=None,
        score=80.0,
        is_verified=False,
        submitted_year=None,
        nmrr_id=None,
    ):
        project = InnovationProject.objects.create(
            officer=user,
            project_title=title,
            innovation_type=innovation_type,
            category=(
                category if category is not None
                else (innovation_type or 'System Compliance')
            ),
            nmrr_id=nmrr_id,
            description='Phase 3B test record',
            score=score,
            is_verified=is_verified,
        )
        if submitted_year is not None and submitted_year != self.year:
            InnovationProject.objects.filter(pk=project.pk).update(
                submitted_at=timezone.make_aware(
                    datetime(submitted_year, 6, 1, 12, 0)
                )
            )
            project.refresh_from_db()
        return project

    def test_empty_operational_dashboard_uses_no_data_and_has_no_fake_content(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        context = response.context
        self.assertIsNone(context['prod_cleared_avg'])
        self.assertIsNone(context['prod_consult_avg'])
        self.assertIsNone(context['prod_punctuality_avg'])
        self.assertEqual(context['prod_cleared_avg_display'], 'No data')
        self.assertEqual(context['prod_consult_avg_display'], 'No data')
        self.assertEqual(context['prod_punctuality_avg_display'], 'No data')
        self.assertEqual(context['ops_total_projects'], 0)
        self.assertEqual(context['ops_average_score_display'], 'No data')
        self.assertEqual(context['ops_dept_rank_display'], '#1 of 1')

        self.assertContains(response, 'No data')
        self.assertContains(response, 'No productivity records found.')
        self.assertContains(response, 'No research projects recorded')
        self.assertContains(response, 'No clinical trials recorded')
        self.assertContains(response, 'No technology adoption records')
        self.assertContains(response, 'No publications recorded')
        self.assertNotContains(response, 'WISN Workload')
        self.assertNotContains(response, 'Top 15%')
        self.assertNotContains(response, 'Hospital HIS/EMR')
        self.assertNotContains(response, 'MedJ Malaysia')
        self.assertNotContains(response, 'Dr. Chong Wei')
        self.assertNotContains(response, 'Dr. Siti Fazura')
        self.assertNotContains(response, 'Annual Target')
        self.assertNotContains(response, 'Total Innov. Points')


    def test_productivity_averages_skip_null_ward_and_procedure_values(self):
        today = timezone.localdate()
        ProductivityLog.objects.create(
            officer=self.user,
            record_date=today - timedelta(days=1),
            patients_cleared=40,
            ward_cases=None,
            procedures_completed=None,
            avg_consult_minutes=15.0,
            punctuality_rate=100.0,
            punctuality_status='On-Time',
        )
        ProductivityLog.objects.create(
            officer=self.user,
            record_date=today,
            patients_cleared=20,
            ward_cases=10,
            procedures_completed=4,
            avg_consult_minutes=10.0,
            punctuality_rate=70.0,
            punctuality_status='Ward Round Extension',
        )

        response = self.client.get(self.url)

        context = response.context
        self.assertEqual(context['prod_cleared_avg'], 30.0)
        self.assertEqual(context['prod_ward_avg'], 10.0)
        self.assertEqual(context['prod_procedure_avg'], 4.0)
        self.assertEqual(context['prod_consult_avg'], 12.5)
        self.assertEqual(context['prod_punctuality_avg'], 85.0)
        self.assertEqual(context['prod_punctuality_avg_display'], '85%')
        self.assertTrue(context['prod_has_logs'])
        self.assertEqual(len(context['prod_recent_logs']), 2)
        self.assertEqual(
            context['prod_recent_logs'][1]['ward_cases_display'],
            'Not recorded',
        )
        self.assertEqual(
            context['prod_recent_logs'][1]['procedures_display'],
            'Not recorded',
        )
        self.assertContains(response, 'Not recorded')

    def test_recent_productivity_table_is_limited_to_fourteen_logs(self):
        today = timezone.localdate()
        ProductivityLog.objects.bulk_create([
            ProductivityLog(
                officer=self.user,
                record_date=today - timedelta(days=index),
                patients_cleared=index + 1,
                ward_cases=index,
                procedures_completed=index,
                avg_consult_minutes=10.0 + index,
                punctuality_rate=90.0,
                punctuality_status='On-Time',
            )
            for index in range(16)
        ])

        response = self.client.get(self.url)

        recent_logs = response.context['prod_recent_logs']
        self.assertEqual(len(recent_logs), 14)
        self.assertEqual(
            recent_logs[0]['record_date_display'],
            today.strftime('%d %b %Y'),
        )
        self.assertEqual(
            recent_logs[-1]['record_date_display'],
            (today - timedelta(days=13)).strftime('%d %b %Y'),
        )


    def test_innovation_projects_are_classified_and_summarised(self):
        self._project(
            self.user, 'Local Sepsis Registry',
            innovation_type='Research', score=90.0, is_verified=True,
            nmrr_id='NMRR-26-001',
        )
        self._project(
            self.user, 'CRC-26-001A',
            innovation_type='Clinical Trial', score=70.0,
        )
        self._project(
            self.user, 'Legacy Technology Record',
            innovation_type=None, category='Technology Adaptation', score=80.0,
        )
        self._project(
            self.user, 'Community Screening Paper',
            innovation_type='Publication', score=60.0,
        )
        self._project(
            self.user, 'Legacy Operational Record',
            innovation_type=None, category='System Compliance', score=50.0,
        )
        self._project(
            self.user, 'Prior Year Record',
            innovation_type='Research', score=0.0,
            submitted_year=self.year - 1,
        )

        response = self.client.get(self.url)

        context = response.context
        self.assertEqual(context['ops_total_projects'], 6)
        self.assertEqual(context['ops_verified_projects'], 1)
        self.assertEqual(context['ops_pending_projects'], 5)
        self.assertEqual(context['ops_publication_count'], 1)
        self.assertEqual(context['ops_uncategorised_count'], 1)
        self.assertAlmostEqual(context['ops_average_score'], 58.3333, places=3)
        self.assertFalse(context['ops_average_score_is_provisional'])
        self.assertEqual(len(context['ops_research_projects']), 2)
        self.assertEqual(len(context['ops_trial_projects']), 1)
        self.assertEqual(len(context['ops_technology_projects']), 1)
        self.assertEqual(len(context['ops_publication_projects']), 1)
        self.assertEqual(len(context['ops_uncategorised_projects']), 1)
        self.assertEqual(
            context['ops_research_projects'][0]['status_label'],
            'Verified',
        )
        self.assertEqual(
            context['ops_technology_projects'][0]['proficiency_display'],
            'Not recorded',
        )
        self.assertContains(response, 'Local Sepsis Registry')
        self.assertContains(response, 'NMRR-26-001')
        self.assertContains(response, 'Prior Year Record')
        self.assertContains(response, 'General/Uncategorised')

    def test_average_score_is_provisional_without_verified_projects(self):
        self._project(
            self.user, 'Unverified Research',
            innovation_type='Research', score=82.0,
        )

        response = self.client.get(self.url)

        context = response.context
        self.assertEqual(context['ops_average_score'], 82.0)
        self.assertTrue(context['ops_average_score_is_provisional'])
        self.assertContains(response, 'Provisional')
        self.assertContains(response, 'No verified project yet')

    def test_department_leaderboard_uses_counts_and_tie_breaks(self):
        peer_high = self._staff('rank_high', first_name='Bella')
        peer_verified = self._staff('rank_verified', first_name='Cheng')
        other_department = self._staff(
            'rank_other', department='FMS', first_name='Zara',
        )

        for index in range(2):
            self._project(
                self.user, f'Current User Project {index}',
                innovation_type='Research',
            )
        self._project(
            self.user, 'Prior Year Project',
            innovation_type='Research', submitted_year=self.year - 1,
        )
        for index in range(3):
            self._project(
                peer_high, f'High Performer Project {index}',
                innovation_type='Research',
            )
        self._project(
            peer_verified, 'Verified Peer Project',
            innovation_type='Research', is_verified=True,
        )
        self._project(
            peer_verified, 'Unverified Peer Project',
            innovation_type='Research',
        )
        for index in range(5):
            self._project(
                other_department, f'Other Department Project {index}',
                innovation_type='Research',
            )

        response = self.client.get(self.url)

        context = response.context
        self.assertEqual(context['ops_dept_rank_total'], 3)
        self.assertEqual(context['ops_dept_rank'], 3)
        self.assertEqual(context['ops_dept_rank_display'], '#3 of 3')
        self.assertEqual(context['ops_total_projects'], 3)
        self.assertEqual(
            [row['user_id'] for row in context['ops_leaderboard']],
            [peer_high.id, peer_verified.id, self.user.id],
        )
        self.assertNotIn(
            other_department.id,
            [row['user_id'] for row in context['ops_leaderboard']],
        )
        self.assertContains(response, 'Dept Leaderboard (%s)' % self.year)

    def test_department_leaderboard_breaks_ties_by_publication_then_name(self):
        publication_peer = self._staff(
            'rank_publication', first_name='Zara',
        )
        name_peer = self._staff('rank_name', first_name='Ahmad')

        self._project(
            self.user, 'Current User Research',
            innovation_type='Research',
        )
        self._project(
            publication_peer, 'Peer Publication',
            innovation_type='Publication',
        )
        self._project(
            name_peer, 'Peer Research',
            innovation_type='Research',
        )

        response = self.client.get(self.url)

        context = response.context
        self.assertEqual(context['ops_dept_rank_display'], '#3 of 3')
        self.assertEqual(
            [row['name'] for row in context['ops_leaderboard']],
            ['Zara', 'Ahmad', 'Amir Hakim'],
        )


class InterpersonalDashboardPhase3CTests(TestCase):
    def setUp(self):
        # Keep response.context usable under Django 5.0.3 + Python 3.14.
        copy_patcher = patch('django.test.client.copy', new=lambda value: value)
        copy_patcher.start()
        self.addCleanup(copy_patcher.stop)

        self.year = timezone.localdate().year
        self.user = self._staff(
            'interpersonal_dashboard_user',
            first_name='Aina',
            last_name='Rahman',
        )
        self.client.force_login(self.user)
        self.url = reverse('staff_dashboard') + '?section=Interpersonal'

    def _staff(self, username, department='CTS', first_name='', last_name=''):
        user = User.objects.create_user(
            username=username,
            first_name=first_name,
            last_name=last_name,
        )
        UserProfile.objects.create(
            user=user,
            role='STAFF',
            department=department,
        )
        return user

    def test_empty_interpersonal_dashboard_uses_no_data_and_no_fake_content(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        context = response.context
        self.assertFalse(context['mypsq_has_data'])
        self.assertIsNone(context['mypsq_avg_score'])
        self.assertIsNone(context['mypsq_pct'])
        self.assertEqual(context['mypsq_count'], 0)
        self.assertEqual(context['mypsq_latest_date_display'], 'No data')
        self.assertEqual(context['ei_total_reflections'], 0)
        self.assertEqual(context['ei_domain_count'], 0)
        self.assertIsNone(context['ei_composite'])
        self.assertEqual(context['ei_composite_display'], 'No data')
        self.assertEqual(context['ei_streak_weeks'], 0)
        self.assertEqual(context['kudos_feed_total'], 0)
        self.assertEqual(context['kudos_given_points'], 0)
        self.assertEqual(context['kudos_received_points'], 0)
        self.assertEqual(context['mentorship_mentor_count'], 0)
        self.assertEqual(context['mentorship_mentee_count'], 0)
        self.assertEqual(context['mentorship_mentor_hours'], 0)
        self.assertEqual(context['mentorship_signoff_display'], 'No data')

        self.assertContains(response, 'No data')
        self.assertContains(response, 'No active streak')
        self.assertContains(
            response,
            'No patient feedback recorded yet.',
        )
        self.assertContains(
            response,
            'No emotional reflections recorded yet.',
        )
        self.assertContains(
            response,
            'No personal Kudos activity recorded yet.',
        )
        self.assertContains(
            response,
            'No mentorship sessions recorded yet.',
        )
        self.assertContains(response, 'value="50"')
        self.assertNotContains(response, 'MoH Guidelines')
        self.assertNotContains(response, 'Karma')
        self.assertNotContains(response, 'Dr. Siti Fazura')
        self.assertNotContains(response, 'Dr. Chong Wei')
        self.assertNotContains(response, 'HO Dr. Farhan')
        self.assertNotContains(response, 'Verified by HOD')
        self.assertNotContains(response, 'AI Insight')
        self.assertNotContains(response, 'Burnout Risk Index')
        self.assertNotContains(response, '100% Real-Time')
        self.assertNotContains(response, '4.8')

    def test_ei_uses_all_time_domain_averages_without_zeroing_missing_values(self):
        today = timezone.localdate()
        EmotionalReflection.objects.create(
            officer=self.user,
            record_date=today,
            self_awareness_score=80,
            self_regulation_score=70,
            empathy_score=None,
            deescalation_score=60,
        )
        EmotionalReflection.objects.create(
            officer=self.user,
            record_date=today - timedelta(days=7),
            self_awareness_score=90,
            self_regulation_score=None,
            empathy_score=80,
            deescalation_score=None,
        )

        response = self.client.get(self.url)

        context = response.context
        cards = {
            card['key']: card for card in context['ei_domain_cards']
        }
        self.assertEqual(cards['self_awareness_score']['average'], 85)
        self.assertEqual(cards['self_regulation_score']['average'], 70)
        self.assertEqual(cards['empathy_score']['average'], 80)
        self.assertEqual(cards['deescalation_score']['average'], 60)
        self.assertEqual(cards['self_awareness_score']['recorded_count'], 2)
        self.assertEqual(cards['self_regulation_score']['recorded_count'], 1)
        self.assertEqual(context['ei_domain_count'], 4)
        self.assertEqual(context['ei_composite'], 74)
        self.assertFalse(context['ei_composite_is_provisional'])
        self.assertEqual(
            context['ei_composite_status_display'],
            'Complete (4/4 domains)',
        )
        self.assertEqual(
            [
                score['display']
                for score in context['ei_reflections'][0]['scores']
            ],
            ['80%', '70%', 'Not recorded', '60%'],
        )
        self.assertContains(response, 'Complete (4/4 domains)')
        self.assertContains(response, '85%')

    def test_ei_composite_is_provisional_when_legacy_domains_are_missing(self):
        EmotionalReflection.objects.create(
            officer=self.user,
            self_awareness_score=85,
        )

        response = self.client.get(self.url)

        context = response.context
        self.assertEqual(context['ei_domain_count'], 1)
        self.assertEqual(context['ei_composite'], 85)
        self.assertTrue(context['ei_composite_is_provisional'])
        self.assertEqual(
            context['ei_composite_status_display'],
            'Provisional (1/4 domains)',
        )
        self.assertContains(response, 'Provisional (1/4 domains)')

    def test_ei_weekly_streak_counts_unique_weeks_and_allows_one_week_grace(self):
        today = timezone.localdate()
        current_week_start = today - timedelta(days=today.weekday())
        previous_week_start = current_week_start - timedelta(days=7)
        EmotionalReflection.objects.create(
            officer=self.user,
            record_date=previous_week_start,
        )
        EmotionalReflection.objects.create(
            officer=self.user,
            record_date=previous_week_start + timedelta(days=1),
        )
        EmotionalReflection.objects.create(
            officer=self.user,
            record_date=previous_week_start - timedelta(days=7),
        )

        response = self.client.get(self.url)

        self.assertEqual(response.context['ei_streak_weeks'], 2)
        self.assertContains(response, 'Consecutive Weeks')

        EmotionalReflection.objects.create(
            officer=self.user,
            record_date=today,
        )
        response = self.client.get(self.url)

        self.assertEqual(response.context['ei_streak_weeks'], 3)

    def test_kudos_feed_is_personal_two_way_and_reports_real_points(self):
        peer_one = self._staff(
            'kudos_peer_one',
            first_name='Bella',
            last_name='Lim',
        )
        peer_two = self._staff(
            'kudos_peer_two',
            first_name='Chen',
            last_name='Wei',
        )
        PeerKudos.objects.create(
            sender=peer_one,
            receiver=self.user,
            category='Clinical Knowledge Sharing & CME',
            points=7,
            message='Terima kasih atas perkongsian CME.',
        )
        PeerKudos.objects.create(
            sender=self.user,
            receiver=peer_two,
            category='Shift Cover & Ward Relief',
            points=10,
            message='Bantuan liputan wad yang pantas.',
        )
        PeerKudos.objects.create(
            sender=peer_one,
            receiver=peer_two,
            category='Private',
            points=50,
            message='Private peer message',
        )

        response = self.client.get(self.url)

        context = response.context
        self.assertEqual(context['kudos_feed_total'], 2)
        self.assertEqual(context['kudos_given_count'], 1)
        self.assertEqual(context['kudos_received_count'], 1)
        self.assertEqual(context['kudos_given_points'], 10)
        self.assertEqual(context['kudos_received_points'], 7)
        self.assertEqual(
            {item['direction_label'] for item in context['kudos_feed']},
            {'Sent', 'Received'},
        )
        self.assertContains(response, 'Your Personal Two-Way Kudos Activity')
        self.assertContains(response, 'Dr. Bella Lim awarded you')
        self.assertContains(response, 'You awarded Dr. Chen Wei')
        self.assertContains(response, 'Terima kasih atas perkongsian CME.')
        self.assertContains(response, 'Bantuan liputan wad yang pantas.')
        self.assertNotContains(response, 'Private peer message')
        self.assertNotContains(response, 'Karma')

    def test_mentorship_uses_mentor_hours_active_mentees_and_verified_flags(self):
        verifier = self._staff(
            'mentorship_verifier',
            first_name='Head',
            last_name='Verifier',
        )
        today = timezone.localdate()
        MentorshipSession.objects.create(
            mentor=self.user,
            participant_role='mentor',
            mentee_name=' HO Alice ',
            topic='Bedside Clinical Supervision',
            notes='Sesi pertama.',
            session_date=today,
            duration_hours=2.0,
            is_verified=False,
        )
        MentorshipSession.objects.create(
            mentor=self.user,
            participant_role='mentor',
            mentee_name='ho alice',
            topic='Audit & Research Guidance',
            notes='Sesi kedua tanpa verifier.',
            session_date=today - timedelta(days=1),
            duration_hours=1.5,
            is_verified=True,
            verified_by=None,
        )
        MentorshipSession.objects.create(
            mentor=self.user,
            participant_role='mentor',
            mentee_name='Bob',
            topic='Postgraduate & Parallel Pathway',
            notes='Sesi tahun lepas.',
            session_date=date(self.year - 1, 6, 1),
            duration_hours=4.0,
            is_verified=True,
            verified_by=verifier,
        )
        MentorshipSession.objects.create(
            mentor=self.user,
            participant_role='mentee',
            mentee_name='Dr. Mentor',
            topic='On-Call Coping & Stress Adaptation',
            notes='Sesi sebagai mentee.',
            session_date=today,
            duration_hours=3.0,
            is_verified=True,
        )

        response = self.client.get(self.url)

        context = response.context
        self.assertEqual(context['mentorship_mentor_count'], 3)
        self.assertEqual(context['mentorship_mentee_count'], 1)
        self.assertEqual(context['mentorship_mentor_hours'], 7.5)
        self.assertEqual(context['mentorship_mentor_hours_display'], '7.5')
        self.assertEqual(context['mentorship_mentee_hours'], 3.0)
        self.assertEqual(context['mentorship_mentee_hours_display'], '3')
        self.assertEqual(context['mentorship_active_mentees'], 1)
        self.assertEqual(context['mentorship_mentor_verified_count'], 2)
        self.assertEqual(context['mentorship_signoff_rate'], 67)
        self.assertEqual(context['mentorship_signoff_display'], '67%')

        statuses = [
            (session['participant_name'], session['status_display'])
            for session in context['mentorship_sessions']
        ]
        self.assertIn((' HO Alice ', 'Pending Verification'), statuses)
        self.assertIn(('ho alice', 'Verified'), statuses)
        self.assertIn(
            ('Bob', 'Verified by Dr. Head Verifier'),
            statuses,
        )
        self.assertIn(('Dr. Mentor', 'Verified'), statuses)
        self.assertContains(response, 'Active Mentees (%s)' % self.year)
        self.assertContains(response, 'As Mentee')
        self.assertContains(response, 'Verified by Dr. Head Verifier')
        self.assertNotContains(response, 'Verified by HOD')

    def test_mypsq_uses_recorded_feedback_average_and_comments(self):
        MyPSQFeedback.objects.create(
            doctor=self.user,
            patient_category='Outpatient',
            q1_general=4,
            q2_technical=4,
            q3_interpersonal=4,
            q4_communication=4,
            q5_financial=4,
            q6_time=4,
            q7_accessibility=4,
            patient_comments='Penjelasan doktor sangat jelas.',
        )
        MyPSQFeedback.objects.create(
            doctor=self.user,
            patient_category='Inpatient',
            q1_general=5,
            q2_technical=5,
            q3_interpersonal=5,
            q4_communication=5,
            q5_financial=5,
            q6_time=5,
            q7_accessibility=5,
            patient_comments='',
        )

        response = self.client.get(self.url)

        context = response.context
        self.assertTrue(context['mypsq_has_data'])
        self.assertEqual(context['mypsq_count'], 2)
        self.assertEqual(context['mypsq_avg_score'], 4.5)
        self.assertEqual(context['mypsq_pct'], 90)
        self.assertEqual(
            context['mypsq_latest_date_display'],
            timezone.localdate().strftime('%d %b %Y'),
        )
        self.assertEqual(len(context['mypsq_comments']), 1)
        self.assertContains(response, '4.5')
        self.assertContains(response, 'Penjelasan doktor sangat jelas.')
        self.assertNotContains(
            response,
            'The doctor explained the treatment plan very clearly',
        )


class InterpersonalPersistencePhase2Tests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='interpersonal_test_user')
        UserProfile.objects.create(
            user=self.user,
            role='STAFF',
            department='CTS',
        )
        self.client.force_login(self.user)
        self.url = reverse('staff_dashboard') + '?section=Interpersonal'

    def _ei_data(self):
        return {
            'form_type': 'int_ei',
            'ei_self_awareness': '85',
            'ei_regulation': '80',
            'ei_empathy': '90',
            'ei_deescalation': '82',
            'ei_trigger': 'Aggressive Patient/Attendant',
            'ei_coping': 'De-escalation Protocol Applied',
            'ei_reflection': 'Saya mengawal emosi dan berbincang dengan rakan sekerja.',
        }

    def _post_ei(self, data=None):
        return self.client.post(
            self.url,
            data if data is not None else self._ei_data(),
        )

    def _mentor_data(self):
        return {
            'form_type': 'int_mentor',
            'mentor_role': 'mentee',
            'mentee_name': 'Dr. Azlan Shah',
            'mentor_date': '2026-10-18',
            'mentor_duration': '2 Hours',
            'mentor_domain': 'Postgraduate & Parallel Pathway',
            'mentor_notes': 'Perbincangan tindakan susulan.',
        }

    def _post_mentor(self, data=None):
        return self.client.post(
            self.url,
            data if data is not None else self._mentor_data(),
        )

    def test_ei_persists_four_domain_scores_and_structured_fields(self):
        response = self._post_ei()

        self.assertEqual(response.status_code, 302)
        reflection = EmotionalReflection.objects.get(officer=self.user)
        self.assertEqual(reflection.self_awareness_score, 85)
        self.assertEqual(reflection.self_regulation_score, 80)
        self.assertEqual(reflection.empathy_score, 90)
        self.assertEqual(reflection.deescalation_score, 82)
        self.assertEqual(
            reflection.trigger_event,
            'Aggressive Patient/Attendant',
        )
        self.assertEqual(
            reflection.coping_strategy,
            'De-escalation Protocol Applied',
        )
        self.assertEqual(
            reflection.reflection_notes,
            'Saya mengawal emosi dan berbincang dengan rakan sekerja.',
        )
        self.assertEqual(reflection.record_date, timezone.localdate())

    def test_ei_rejects_missing_or_out_of_range_scores(self):
        for invalid_score in ('', '-1', '101', 'not-a-number'):
            with self.subTest(score=invalid_score):
                EmotionalReflection.objects.filter(officer=self.user).delete()
                data = self._ei_data()
                data['ei_empathy'] = invalid_score

                response = self._post_ei(data)

                self.assertEqual(response.status_code, 302)
                self.assertFalse(
                    EmotionalReflection.objects.filter(officer=self.user).exists()
                )

    def test_ei_rejects_missing_or_invalid_required_choices(self):
        for field_name in ('ei_trigger', 'ei_coping', 'ei_reflection'):
            with self.subTest(missing=field_name):
                EmotionalReflection.objects.filter(officer=self.user).delete()
                data = self._ei_data()
                data.pop(field_name)

                response = self._post_ei(data)

                self.assertEqual(response.status_code, 302)
                self.assertFalse(
                    EmotionalReflection.objects.filter(officer=self.user).exists()
                )

        for field_name in ('ei_trigger', 'ei_coping'):
            with self.subTest(invalid=field_name):
                EmotionalReflection.objects.filter(officer=self.user).delete()
                data = self._ei_data()
                data[field_name] = 'Pilihan Tidak Sah'

                response = self._post_ei(data)

                self.assertEqual(response.status_code, 302)
                self.assertFalse(
                    EmotionalReflection.objects.filter(officer=self.user).exists()
                )

    def test_ei_accepts_boundary_scores(self):
        data = self._ei_data()
        data.update({
            'ei_self_awareness': '0',
            'ei_regulation': '100',
            'ei_empathy': '0',
            'ei_deescalation': '100',
        })

        response = self._post_ei(data)

        self.assertEqual(response.status_code, 302)
        reflection = EmotionalReflection.objects.get(officer=self.user)
        self.assertEqual(reflection.self_awareness_score, 0)
        self.assertEqual(reflection.self_regulation_score, 100)
        self.assertEqual(reflection.empathy_score, 0)
        self.assertEqual(reflection.deescalation_score, 100)

    def test_mentorship_persists_role_and_full_notes_without_truncation(self):
        long_notes = (
            'Perbincangan terperinci mengenai pengurusan pesakit kritikal, '
            'komunikasi dengan keluarga, dan perancangan tindakan susulan. '
        ) * 4
        data = self._mentor_data()
        data['mentor_notes'] = long_notes

        response = self._post_mentor(data)

        self.assertEqual(response.status_code, 302)
        session = MentorshipSession.objects.get(mentor=self.user)
        self.assertEqual(session.participant_role, 'mentee')
        self.assertEqual(session.mentee_name, 'Dr. Azlan Shah')
        self.assertEqual(
            session.topic,
            'Postgraduate & Parallel Pathway',
        )
        self.assertEqual(session.notes, long_notes.strip())
        self.assertGreater(len(session.notes), 200)
        self.assertNotIn('(Mentee)', session.mentee_name)
        self.assertNotIn(session.notes, session.topic)
        self.assertEqual(session.session_date, date(2026, 10, 18))
        self.assertEqual(session.duration_hours, 2.0)

    def test_mentorship_rejects_invalid_or_missing_required_fields(self):
        invalid_cases = (
            ('mentor_role', 'supervisor'),
            ('mentor_domain', 'Unknown Domain'),
            ('mentor_date', '2026-02-30'),
            ('mentor_duration', '3 Hours'),
            ('mentee_name', ''),
            ('mentor_notes', ''),
        )

        for field_name, invalid_value in invalid_cases:
            with self.subTest(field=field_name):
                MentorshipSession.objects.filter(mentor=self.user).delete()
                data = self._mentor_data()
                data[field_name] = invalid_value

                response = self._post_mentor(data)

                self.assertEqual(response.status_code, 302)
                self.assertFalse(
                    MentorshipSession.objects.filter(mentor=self.user).exists()
                )

        for field_name in (
            'mentor_role',
            'mentee_name',
            'mentor_date',
            'mentor_duration',
            'mentor_domain',
            'mentor_notes',
        ):
            with self.subTest(missing=field_name):
                MentorshipSession.objects.filter(mentor=self.user).delete()
                data = self._mentor_data()
                data.pop(field_name)

                response = self._post_mentor(data)

                self.assertEqual(response.status_code, 302)
                self.assertFalse(
                    MentorshipSession.objects.filter(mentor=self.user).exists()
                )

    def test_ei_domain_average_returns_none_for_missing_legacy_domains(self):
        legacy = EmotionalReflection.objects.create(
            officer=self.user,
            self_awareness_score=85,
        )
        current = EmotionalReflection.objects.create(
            officer=self.user,
            self_awareness_score=80,
            self_regulation_score=70,
            empathy_score=90,
            deescalation_score=60,
        )

        self.assertEqual(
            _average_reflection_domains([legacy]),
            [85, None, None, None],
        )
        self.assertEqual(
            _average_reflection_domains([current]),
            [80, 70, 90, 60],
        )


class HODFeedbackAcknowledgmentPhase2Tests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='feedback_receiver')
        UserProfile.objects.create(
            user=self.user,
            role='STAFF',
            department='CTS',
        )
        self.hod = User.objects.create_user(username='feedback_sender')
        UserProfile.objects.create(
            user=self.hod,
            role='HOD',
            department='CTS',
        )
        self.client.force_login(self.user)
        self.url = reverse('staff_dashboard') + '?section=Dashboard'
        self.feedback = HODFeedback.objects.create(
            sender_hod=self.hod,
            receiver_staff=self.user,
            severity='INFO',
            message_text='Sila lengkapkan dokumentasi klinikal.',
        )

    def _post_ack(self, feedback_id):
        return self.client.post(
            self.url,
            {
                'form_type': 'acknowledge_feedback',
                'feedback_id': str(feedback_id),
            },
        )

    def test_acknowledgment_sets_boolean_and_timestamp(self):
        response = self._post_ack(self.feedback.id)

        self.assertEqual(response.status_code, 302)
        self.feedback.refresh_from_db()
        self.assertTrue(self.feedback.is_acknowledged)
        self.assertIsNotNone(self.feedback.acknowledged_at)
        self.assertTrue(timezone.is_aware(self.feedback.acknowledged_at))

    def test_acknowledgment_is_idempotent(self):
        self._post_ack(self.feedback.id)
        self.feedback.refresh_from_db()
        first_acknowledged_at = self.feedback.acknowledged_at

        response = self._post_ack(self.feedback.id)

        self.assertEqual(response.status_code, 302)
        self.feedback.refresh_from_db()
        self.assertTrue(self.feedback.is_acknowledged)
        self.assertEqual(self.feedback.acknowledged_at, first_acknowledged_at)

    def test_staff_cannot_acknowledge_another_staff_feedback(self):
        other_staff = User.objects.create_user(username='other_feedback_receiver')
        UserProfile.objects.create(
            user=other_staff,
            role='STAFF',
            department='CTS',
        )
        other_feedback = HODFeedback.objects.create(
            sender_hod=self.hod,
            receiver_staff=other_staff,
            severity='WARNING',
            message_text='Maklum balas untuk staf lain.',
        )

        response = self._post_ack(other_feedback.id)

        self.assertEqual(response.status_code, 302)
        other_feedback.refresh_from_db()
        self.assertFalse(other_feedback.is_acknowledged)
        self.assertIsNone(other_feedback.acknowledged_at)

    def test_invalid_or_missing_feedback_id_is_ignored(self):
        for invalid_id in ('abc', '', '999999'):
            with self.subTest(feedback_id=invalid_id):
                self.feedback.refresh_from_db()
                self.feedback.is_acknowledged = False
                self.feedback.acknowledged_at = None
                self.feedback.save(
                    update_fields=['is_acknowledged', 'acknowledged_at']
                )

                response = self._post_ack(invalid_id)

                self.assertEqual(response.status_code, 302)
                self.feedback.refresh_from_db()
                self.assertFalse(self.feedback.is_acknowledged)
                self.assertIsNone(self.feedback.acknowledged_at)


class PRIScoreCompatibilityPhase2Tests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='pri_compatibility_user')

    def test_new_ei_record_uses_average_of_four_domains(self):
        EmotionalReflection.objects.create(
            officer=self.user,
            self_awareness_score=80,
            self_regulation_score=70,
            empathy_score=90,
            deescalation_score=60,
        )

        result = calculate_pri_score(self.user)

        self.assertEqual(result['score_interpersonal'], 83)

    def test_legacy_ei_record_keeps_self_awareness_contribution(self):
        EmotionalReflection.objects.create(
            officer=self.user,
            self_awareness_score=85,
        )

        result = calculate_pri_score(self.user)

        self.assertEqual(result['score_interpersonal'], 86)

    def test_mixed_legacy_and_new_ei_records_average_available_domains(self):
        EmotionalReflection.objects.create(
            officer=self.user,
            self_awareness_score=85,
        )
        EmotionalReflection.objects.create(
            officer=self.user,
            self_awareness_score=80,
            self_regulation_score=70,
            empathy_score=90,
            deescalation_score=60,
        )

        result = calculate_pri_score(self.user)

        self.assertEqual(result['score_interpersonal'], 85)


class ModuleScoreEngineTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='module_score_user',
            password='test-pass',
        )
        self.today = timezone.localdate()

    def _aware(self, day_offset=0, hour=9, minute=0):
        day = self.today + timedelta(days=day_offset)
        naive = datetime.combine(day, datetime.min.time())
        naive += timedelta(hours=hour, minutes=minute)
        return timezone.make_aware(naive)

    def _assert_module(self, result, key, score, status='OK', n=None):
        module = result['by_key'][key]
        self.assertEqual(module['status'], status)
        if score is None:
            self.assertIsNone(module['score'])
        else:
            self.assertAlmostEqual(module['score'], score, places=1)
        if n is not None:
            self.assertEqual(module['n'], n)
        return module

    def test_all_module_formulas_and_return_contract(self):
        for offset, verified, successful in (
            (0, True, True),
            (1, True, True),
            (2, True, False),
            (3, False, True),
        ):
            ProcedureLog.objects.create(
                officer=self.user,
                procedure_date=self.today - timedelta(days=offset),
                is_verified=verified,
                is_successful=successful,
            )

        for offset, start_hour, end_hour, on_time in (
            (0, 9, 11, True),
            (1, 9, 12, True),
            (2, 9, 14, False),
        ):
            DocumentationLog.objects.create(
                officer=self.user,
                patient_id=f'DOC-{offset}',
                encounter_at=self._aware(-offset, start_hour),
                completed_at=self._aware(-offset, end_hour),
                delay_hours=end_hour - start_hour,
                is_ontime=on_time,
                is_completed=True,
            )
        DocumentationLog.objects.create(
            officer=self.user,
            patient_id='DOC-PENDING',
            encounter_at=self._aware(0, 8),
            completed_at=None,
            is_completed=False,
        )

        CPDClaim.objects.create(
            officer=self.user,
            topic='Approved CPD',
            points=30,
            event_date=self.today,
            status='APPROVED',
        )
        CPDClaim.objects.create(
            officer=self.user,
            topic='Pending CPD',
            points=20,
            event_date=self.today,
            status='PENDING',
        )
        CPDClaim.objects.create(
            officer=self.user,
            topic='Rejected CPD',
            points=10,
            event_date=self.today,
            status='REJECTED',
        )

        declaration = PolicyDeclaration.objects.create(
            officer=self.user,
            year=self.today.year,
        )
        for question_number in range(1, 21):
            PolicyItemResponse.objects.create(
                declaration=declaration,
                question_number=question_number,
                compliance_ratio=0.9,
            )

        for offset in range(30):
            ProductivityLog.objects.create(
                officer=self.user,
                record_date=self.today - timedelta(days=offset),
                patients_cleared=40,
            )


        InnovationProject.objects.create(
            officer=self.user,
            project_title='Verified innovation',
            description='Test',
            final_points=10,
            score=80,
            is_verified=True,
        )
        InnovationProject.objects.create(
            officer=self.user,
            project_title='Pending innovation',
            description='Test',
            final_points=5,
            score=80,
            is_verified=False,
        )

        MyPSQFeedback.objects.create(
            doctor=self.user,
            q1_general=4,
            q2_technical=4,
            q3_interpersonal=4,
            q4_communication=4,
            q5_financial=4,
            q6_time=4,
            q7_accessibility=4,
        )
        EmotionalReflection.objects.create(
            officer=self.user,
            record_date=self.today,
            self_awareness_score=90,
            self_regulation_score=80,
            empathy_score=70,
            deescalation_score=60,
        )

        sender = User.objects.create_user(username='kudos_sender')
        for points in (6, 9, 0):
            PeerKudos.objects.create(
                sender=sender,
                receiver=self.user,
                points=points,
            )

        MentorshipSession.objects.create(
            mentor=self.user,
            participant_role='mentor',
            mentee_name='Mentee A',
            topic='Topic A',
            session_date=self.today,
            duration_hours=2,
            is_verified=True,
        )
        MentorshipSession.objects.create(
            mentor=self.user,
            participant_role='mentor',
            mentee_name='Mentee B',
            topic='Topic B',
            session_date=self.today,
            duration_hours=3,
            is_verified=True,
        )
        MentorshipSession.objects.create(
            mentor=self.user,
            participant_role='mentor',
            mentee_name='Mentee C',
            topic='Topic C',
            session_date=self.today,
            duration_hours=10,
            is_verified=False,
        )
        MentorshipSession.objects.create(
            mentor=self.user,
            participant_role='mentee',
            mentee_name='Mentee D',
            topic='Topic D',
            session_date=self.today,
            duration_hours=10,
            is_verified=True,
        )

        result = get_module_scores(self.user, as_of=self.today)

        self.assertEqual(len(result['modules']), 10)
        self.assertEqual(result['recorded_count'], 10)
        self.assertEqual(result['scorable_count'], 10)
        self.assertEqual(
            set(result['by_key']),
            {module['key'] for module in result['modules']},
        )

        clinical = self._assert_module(
            result, 'clinical_delivery', 66.7, n=3
        )
        self.assertEqual(clinical['raw_value'], 2.0)
        self.assertEqual(clinical['display'], '66.7%')

        documentation = self._assert_module(
            result, 'documentation', 66.7, n=3
        )
        self.assertEqual(documentation['raw_value'], 2.0)

        self._assert_module(result, 'cpd', 60.0, n=1)
        self._assert_module(result, 'policies', 90.0, n=20)
        productivity = self._assert_module(
            result, 'productivity', 52.3, n=30
        )
        self.assertEqual(productivity['raw_value'], 40.0)

        innovation = self._assert_module(
            result, 'innovation', 75.0,
            status='PENDING_VERIFICATION', n=2,
        )
        self.assertTrue(innovation['is_provisional'])
        self.assertEqual(innovation['raw_value'], 15.0)

        self._assert_module(result, 'mypsq', 80.0, n=1)
        self._assert_module(result, 'emotional_intelligence', 75.0, n=1)
        self._assert_module(result, 'teamwork', 50.0, n=3)
        self._assert_module(result, 'mentorship', 50.0, n=2)


    def test_no_data_is_none_while_real_zero_is_zero(self):
        empty = get_module_scores(self.user, as_of=self.today)
        empty_productivity = empty['by_key']['productivity']
        self.assertIsNone(empty_productivity['score'])
        self.assertEqual(empty_productivity['status'], 'NO_DATA')
        self.assertFalse(empty_productivity['has_records'])
        self.assertEqual(empty['recorded_count'], 0)
        self.assertEqual(empty['scorable_count'], 0)

        ProductivityLog.objects.create(
            officer=self.user,
            record_date=self.today,
            patients_cleared=0,
        )
        zero = get_module_scores(self.user, as_of=self.today)
        zero_productivity = zero['by_key']['productivity']
        self.assertEqual(zero_productivity['score'], 0.0)
        self.assertEqual(zero_productivity['status'], 'ZERO')
        self.assertTrue(zero_productivity['has_records'])
        self.assertEqual(zero['recorded_count'], 1)
        self.assertEqual(zero['scorable_count'], 1)

    def test_invalid_documentation_timestamp_is_not_scored(self):
        DocumentationLog.objects.create(
            officer=self.user,
            patient_id='INVALID-TIMESTAMP',
            encounter_at=None,
            completed_at=self._aware(0, 10),
            is_completed=True,
        )
        result = get_module_scores(self.user, as_of=self.today)
        documentation = result['by_key']['documentation']
        self.assertIsNone(documentation['score'])
        self.assertEqual(documentation['status'], 'INVALID_DATA')
        self.assertTrue(documentation['has_records'])

    def test_pending_documentation_and_unverified_procedure_are_pending(self):
        DocumentationLog.objects.create(
            officer=self.user,
            patient_id='PENDING',
            encounter_at=self._aware(0, 9),
            completed_at=None,
            is_completed=False,
        )
        ProcedureLog.objects.create(
            officer=self.user,
            procedure_date=self.today,
            is_verified=False,
            is_successful=True,
        )
        result = get_module_scores(self.user, as_of=self.today)
        self.assertEqual(
            result['by_key']['documentation']['status'],
            'PENDING_VERIFICATION',
        )
        self.assertEqual(
            result['by_key']['clinical_delivery']['status'],
            'PENDING_VERIFICATION',
        )

    def test_policy_domain_fallback_is_provisional(self):
        PolicyDeclaration.objects.create(
            officer=self.user,
            year=self.today.year,
            score_domain_a=80,
            score_domain_b=80,
            score_domain_c=80,
            score_domain_d=80,
            score_domain_e=80,
        )
        result = get_module_scores(self.user, as_of=self.today)
        policies = result['by_key']['policies']
        self.assertAlmostEqual(policies['score'], 80.0, places=1)
        self.assertEqual(policies['status'], 'PROVISIONAL')
        self.assertTrue(policies['is_provisional'])
        self.assertEqual(policies['n'], 5)

    def test_productivity_window_and_duplicate_dates(self):
        ProductivityLog.objects.create(
            officer=self.user,
            record_date=self.today - timedelta(days=40),
            patients_cleared=60,
        )
        ProductivityLog.objects.create(
            officer=self.user,
            record_date=self.today,
            patients_cleared=40,
        )
        ProductivityLog.objects.create(
            officer=self.user,
            record_date=self.today,
            patients_cleared=50,
        )
        result = get_module_scores(self.user, as_of=self.today)
        productivity = result['by_key']['productivity']
        self.assertEqual(productivity['n'], 1)
        self.assertEqual(productivity['raw_value'], 50.0)



class SimulationSeederTests(TestCase):
    username = 'sim_test_doctor'

    def _run(self, *args):
        output = StringIO()
        call_command(
            'seed_simulation_data',
            *args,
            stdout=output,
        )
        return output.getvalue()

    @override_settings(DEBUG=False)
    def test_dry_run_does_not_write(self):
        output = self._run(
            '--dry-run',
            '--force',
            '--username', self.username,
        )
        self.assertIn('[DRY RUN]', output)
        self.assertFalse(
            User.objects.filter(username=self.username).exists()
        )
        self.assertEqual(DocumentationLog.objects.count(), 0)
        self.assertEqual(ProductivityLog.objects.count(), 0)
        self.assertEqual(CPDClaim.objects.count(), 0)

    @override_settings(DEBUG=False)
    def test_refuses_to_run_with_debug_false_without_force(self):
        with self.assertRaises(CommandError):
            self._run(
                '--dry-run',
                '--username', self.username,
            )

    @override_settings(DEBUG=False)
    def test_seed_creates_complete_data_and_scores(self):
        User.objects.create_user(username='dr_mail')
        real_user = User.objects.create_user(username='real_seed_user')
        real_log = ProductivityLog.objects.create(
            officer=real_user,
            record_date=timezone.localdate(),
            patients_cleared=99,
            operational_notes='REAL-RECORD',
        )

        output = self._run(
            '--force',
            '--username', self.username,
            '--days', '14',
            '--seed', '7',
        )
        user = User.objects.get(username=self.username)

        docs = DocumentationLog.objects.filter(
            officer=user,
            patient_id__startswith='SIM-DOC-',
        )
        self.assertTrue(docs.exists())
        for log in docs:
            self.assertIsNotNone(log.encounter_at)
            if log.is_completed:
                self.assertIsNotNone(log.completed_at)
            else:
                self.assertIsNone(log.completed_at)

        productivity = ProductivityLog.objects.filter(
            officer=user,
            operational_notes__startswith='SIM-PROD-',
        )
        self.assertEqual(productivity.count(), 14)
        self.assertEqual(
            productivity.values('record_date').distinct().count(),
            14,
        )

        cpd = CPDClaim.objects.filter(
            officer=user,
            topic__startswith='[SIM]',
        )
        self.assertEqual(cpd.count(), 5)
        self.assertEqual(
            sum(
                claim.points for claim in cpd
                if claim.status == 'APPROVED'
            ),
            45,
        )

        scores = get_module_scores(user, as_of=timezone.localdate())
        self.assertEqual(scores['recorded_count'], 3)
        self.assertEqual(scores['scorable_count'], 3)
        self.assertIsNotNone(
            scores['by_key']['documentation']['score']
        )
        self.assertIsNotNone(
            scores['by_key']['productivity']['score']
        )
        self.assertEqual(scores['by_key']['cpd']['score'], 90.0)

        self.assertIn('SEEDED', output)
        self.assertIn('Module scores', output)
        self.assertTrue(
            ProductivityLog.objects.filter(pk=real_log.pk).exists()
        )
        self.assertEqual(
            ProductivityLog.objects.filter(officer=real_user).count(),
            1,
        )

    @override_settings(DEBUG=False)
    def test_existing_simulation_records_require_reset(self):
        self._run(
            '--force',
            '--username', self.username,
            '--days', '10',
            '--seed', '3',
        )
        with self.assertRaises(CommandError):
            self._run(
                '--force',
                '--username', self.username,
                '--days', '10',
                '--seed', '3',
            )

    @override_settings(DEBUG=False)
    def test_reset_replaces_only_simulation_records(self):
        self._run(
            '--force',
            '--username', self.username,
            '--days', '10',
            '--seed', '3',
        )
        user = User.objects.get(username=self.username)
        before_docs = DocumentationLog.objects.filter(
            officer=user,
            patient_id__startswith='SIM-DOC-',
        ).count()

        other_user = User.objects.create_user(username='other_real_user')
        real_log = DocumentationLog.objects.create(
            officer=other_user,
            patient_id='REAL-1',
            encounter_at=timezone.now(),
            completed_at=timezone.now(),
            is_completed=True,
        )

        output = self._run(
            '--force',
            '--reset',
            '--username', self.username,
            '--days', '10',
            '--seed', '3',
        )

        self.assertEqual(
            DocumentationLog.objects.filter(
                officer=user,
                patient_id__startswith='SIM-DOC-',
            ).count(),
            before_docs,
        )
        self.assertTrue(
            DocumentationLog.objects.filter(pk=real_log.pk).exists()
        )
        self.assertIn('Dipadam', output)
