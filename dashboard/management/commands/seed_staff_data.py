from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import date, timedelta
import random

from dashboard.models import (
    UserProfile, ProcedureLog, PolicyDeclaration, PolicyItemResponse,
    DocumentationLog, CPDClaim, ProductivityLog,
    InnovationProject, MyPSQFeedback, EmotionalReflection,
    PeerKudos, MentorshipSession
)

class Command(BaseCommand):
    help = "Menjana set data realistik 10 modul untuk akaun staf ujian (contoh: dr_mail)"

    def add_arguments(self, parser):
        parser.add_argument(
            '--username',
            type=str,
            default='dr_mail',
            help='Nama pengguna (username) staf sasaran'
        )

    def handle(self, *args, **options):
        username = options['username'].strip()

        # 1. Pastikan Pegawai Wujud
        user, created = User.objects.get_or_create(username=username)
        if created:
            user.set_password('password123')
            user.first_name = 'Ismail'
            user.last_name = 'Razak'
            user.email = f'{username}@moh.gov.my'
            user.save()

        # 2. Kemas Kini / Pastikan UserProfile Lengkap
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.role = 'STAFF'
        profile.department = 'FMS'
        profile.assigned_facility = 'Klinik Kesihatan Medong'
        profile.assigned_state = 'Sarawak'
        profile.phone_number = '+6013-8821940'
        profile.save()

        # Pastikan ada akaun rakan sekerja / HOD sebagai penyumbang Kudos & Feedback
        peer_user, _ = User.objects.get_or_create(username='dr_abu')
        if not peer_user.first_name:
            peer_user.first_name = 'Abu'
            peer_user.last_name = 'Bakar'
            peer_user.save()

        self.stdout.write(self.style.WARNING(f"Menjana data 10 modul bagi: Dr. {user.first_name or user.username}..."))

        # ==========================================
        # TUNJANG 1: CLINICAL EXCELLENCE (4 Modul)
        # ==========================================
        # A. Skills / CUSUM Log
        ProcedureLog.objects.filter(officer=user).delete()
        today = timezone.localdate()
        clinical_cases = [
            # patient_ref, baseline HbA1c, follow-up HbA1c, baseline days ago, follow-up days ago
            ('SEED-DM-001', 11.8, 8.2, 180, 90),
            ('SEED-DM-002', 11.2, 9.0, 170, 80),
            ('SEED-DM-003', 10.9, 10.4, 160, 70),
            ('SEED-DM-004', 12.1, 7.0, 150, 60),
            ('SEED-DM-005', 10.5, 7.8, 140, 50),
            ('SEED-DM-006', 11.4, 8.6, 130, 40),
        ]
        for patient_ref, baseline_hba1c, followup_hba1c, baseline_days_ago, followup_days_ago in clinical_cases:
            baseline_date = today - timedelta(days=baseline_days_ago)
            followup_date = today - timedelta(days=followup_days_ago)
            reduction = round(baseline_hba1c - followup_hba1c, 2)
            achieved = reduction >= 1.0 or followup_hba1c <= 7.0
            common_fields = {
                'officer': user,
                'patient_ref': patient_ref,
                'clinical_domain': 'FMS',
                'facility': profile.assigned_facility,
                'procedure_name': "Diabetes Chronic Review & Complication Screening",
                'primary_diagnosis': "Type 2 Diabetes Mellitus",
                'comorbidities': [],
                'is_successful': True,
                'complication_occurred': False,
                'is_verified': True,
                'verified_at': timezone.now(),
            }
            ProcedureLog.objects.create(
                **common_fields,
                procedure_date=baseline_date,
                hba1c_percent=baseline_hba1c,
                target_achieved=None,
                score=90.0,
            )
            ProcedureLog.objects.create(
                **common_fields,
                procedure_date=followup_date,
                hba1c_percent=followup_hba1c,
                target_achieved=achieved,
                score=92.0 if achieved else 70.0,
            )

        # B. Knowledge & Competency (CPD Claims)
        # Mata dikira automatik daripada Duration + Role oleh CPDClaim.save().
        CPDClaim.objects.filter(officer=user).delete()
        CPDClaim.objects.create(
            officer=user,
            topic="National Diabetes Management Protocol 2026",
            category="CME Workshop",
            kkm_domain='INTERNAL_MEDICINE',
            sub_category='TWO AND A HALF DAYS OR MORE',
            role='ATTENDEE',
            event_date=date.today() - timedelta(days=25),
            status='APPROVED',
        )
        CPDClaim.objects.create(
            officer=user,
            topic="Basic Surgical Skills Workshop",
            category="Workshop",
            kkm_domain='SURGERY',
            sub_category='ONE AND A HALF DAYS',
            role='ATTENDEE',
            event_date=date.today() - timedelta(days=14),
            status='APPROVED',
        )
        CPDClaim.objects.create(
            officer=user,
            topic="Paediatric Asthma Management Update",
            category="Online Quiz",
            kkm_domain='PAEDIATRICS',
            sub_category='FULL DAY',
            role='ATTENDEE',
            event_date=date.today() - timedelta(days=5),
            status='APPROVED',
        )
        CPDClaim.objects.create(
            officer=user,
            topic="Geriatric Falls Prevention Symposium",
            category="CME",
            kkm_domain='GERIATRIC',
            sub_category='FULL DAY',
            role='PRESENTER',
            event_date=date.today(),
            status='PENDING',
        )

        # C. Documentation Monitoring
        DocumentationLog.objects.filter(officer=user).delete()
        for i in range(1, 13):
            is_late = (i in [3, 8])
            delay_hours = 5.0 if is_late else 1.5
            completed_at = timezone.now() - timedelta(days=i)
            encounter_at = completed_at - timedelta(hours=delay_hours)
            DocumentationLog.objects.create(
                officer=user,
                patient_id=f"RN-{202600 + i}",
                encounter_type="Outpatient Consultation",
                encounter_at=encounter_at,
                completed_at=completed_at,
                delay_hours=delay_hours,
                reason_for_delay=(
                    "High patient load in Green Zone triage"
                    if is_late else None
                ),
                delay_reason_category=(
                    "High patient load" if is_late else None
                ),
                delay_reason_remarks=(
                    "Green Zone triage congestion"
                    if is_late else None
                ),
                is_ontime=not is_late,
                is_completed=True,
            )

        # D. Policies & Protocols Compliance
        PolicyDeclaration.objects.filter(officer=user).delete()
        declaration = PolicyDeclaration.objects.create(
            officer=user,
            year=2026,
            score_domain_a=90.0, # Assessment
            score_domain_b=88.0, # Risk & Meds
            score_domain_c=82.0, # Referral
            score_domain_d=94.0, # Diagnostics
            score_domain_e=90.0, # Prevention
            is_verified=True
        )
        policy_ratios = {
            question_number: (
                0.0 if question_number in (9, 17) else 1.0
            )
            for question_number in range(1, 21)
        }
        PolicyItemResponse.objects.bulk_create([
            PolicyItemResponse(
                declaration=declaration,
                question_number=question_number,
                is_compliant=(ratio >= 1.0),
                compliance_ratio=ratio,
                selected_count=1 if ratio >= 1.0 else 0,
                max_count=1,
            )
            for question_number, ratio in policy_ratios.items()
        ])

        # ==========================================
        # TUNJANG 2: OPERATIONAL (2 Modul)
        # ==========================================
        # A. Productivity Logs (14 Hari Berturutan)
        ProductivityLog.objects.filter(officer=user).delete()
        for i in range(14, 0, -1):
            ProductivityLog.objects.create(
                officer=user,
                record_date=date.today() - timedelta(days=i),
                patients_cleared=random.randint(38, 48),
                avg_consult_minutes=round(random.uniform(10.5, 13.5), 1),
                punctuality_rate=float(random.randint(92, 98))
            )

        # B. Innovation & Problem Solving
        InnovationProject.objects.filter(officer=user).delete()
        InnovationProject.objects.create(
            officer=user,
            project_title="Fast-Track Red Zone Diabetic Ketoacidosis Protocol",
            category="Clinical Workflow Improvement",
            description="Mengurangkan purata masa triage pesakit DKA dari 45 minit ke 18 minit di KK Medong.",
            final_points=10.0,
            score=88.0,
            is_verified=True
        )
        InnovationProject.objects.create(
            officer=user,
            project_title="Digital Paperless Retinal Screening Registry",
            category="System Compliance",
            description="Integrasi rujukan digital fundus terus ke klinik pakar mata daerah.",
            final_points=10.0,
            score=84.0,
            is_verified=True
        )

        # ==========================================
        # TUNJANG 3: INTERPERSONAL EXCELLENCE (4 Modul)
        # ==========================================
        # A. Patient Experience (MyPSQ Analytics)
        MyPSQFeedback.objects.filter(doctor=user).delete()
        sample_comments = [
            "Doktor sangat prihatin dan menerangkan dos ubat dengan amat jelas.",
            "Layanan yang sangat mesra dan tidak terburu-buru walaupun klinik ramai.",
            "Rawatan cepat dan teratur.",
            "Sangat berpuas hati dengan penjelasan pemeriksaan darah hari ini."
        ]
        for c in sample_comments:
            MyPSQFeedback.objects.create(
                doctor=user,
                patient_category="Pesakit Luar (FMS)",
                q1_general=random.choice([4, 5]),
                q2_technical=5,
                q3_interpersonal=5,
                q4_communication=random.choice([4, 5]),
                q5_financial=5,
                q6_time=random.choice([4, 5]),
                q7_accessibility=random.choice([4, 5]),
                patient_comments=c
            )

        # B. Stress Management (Emotional Reflection)
        EmotionalReflection.objects.filter(officer=user).delete()
        EmotionalReflection.objects.create(
            officer=user,
            record_date=today - timedelta(days=2),
            self_awareness_score=85,
            self_regulation_score=86,
            empathy_score=88,
            deescalation_score=87,
            reflection_notes="Menangani situasi kecemasan dengan kawalan emosi yang stabil."
        )
        EmotionalReflection.objects.create(
            officer=user,
            record_date=today - timedelta(days=1),
            self_awareness_score=88,
            self_regulation_score=87,
            empathy_score=89,
            deescalation_score=88,
            reflection_notes="Berjaya menguruskan beban pesakit tinggi tanpa rasa terbeban melampau."
        )
        EmotionalReflection.objects.create(
            officer=user,
            record_date=today,
            self_awareness_score=90,
            self_regulation_score=89,
            empathy_score=91,
            deescalation_score=90,
            reflection_notes="Sesi debrief bersama staf paramedik berjalan lancar."
        )

        # C. Teamwork & Collaboration (Peer Kudos)
        PeerKudos.objects.filter(receiver=user).delete()
        PeerKudos.objects.create(sender=peer_user, receiver=user, category="General Clinical", points=5, message="Bantuan pantas ketika zon merah menerima kemasukan pesakit serentak.")
        PeerKudos.objects.create(sender=peer_user, receiver=user, category="Procedural", points=8, message="Bimbingan teknik suntikan insulin sukar yang sangat teliti.")
        PeerKudos.objects.create(sender=peer_user, receiver=user, category="Consultation", points=6, message="Sikap kerjasama yang sentiasa positif dalam pasukan.")

        # D. Training & Mentorship
        MentorshipSession.objects.filter(mentor=user).delete()
        MentorshipSession.objects.create(mentor=user, mentee_name="Dr. Farhana Lee (HO)", topic="Primary Care ECG Interpretation & Arrhythmia", session_date=date.today() - timedelta(days=12), duration_hours=2.0)
        MentorshipSession.objects.create(mentor=user, mentee_name="Dr. Farhana Lee (HO)", topic="Management of Hypertensive Urgency in Clinic", session_date=date.today() - timedelta(days=6), duration_hours=2.0)
        MentorshipSession.objects.create(mentor=user, mentee_name="Dr. Tan Wei Lun (MO Junior)", topic="Maternal Health Protocol & GDM Monitoring", session_date=date.today() - timedelta(days=2), duration_hours=2.0)

        self.stdout.write(self.style.SUCCESS(f"✅ Selesai! Data 10 modul untuk akaun '{username}' berjaya disuntik ke SQLite."))