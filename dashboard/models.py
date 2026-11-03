from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

# ==========================================
# 1. PROFIL PENGGUNA & PERANAN (USER PROFILE)
# ==========================================
class UserProfile(models.Model):
    ROLE_CHOICES = (
        ('HPU_ADMIN', 'Super Admin (HQ)'),
        ('HOD', 'Ketua Jabatan / Pakar'),
        ('STAFF', 'Pegawai Perubatan (MO)'),
        ('AUDITOR', 'Pegawai Audit Kualiti'),
    )

    DEPT_CHOICES = (
        ('CTS', 'Cardiothoracic Surgery (CTS)'),
        ('FMS', 'Family Medicine Specialist (FMS)'),
        ('PEDS', 'Pediatrics'),
        ('ONG', 'Obstetrics & Gynaecology'),
        ('HQ', 'Ibu Pejabat (HQ)'),
    )

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='STAFF')
    department = models.CharField(max_length=10, choices=DEPT_CHOICES, default='CTS')
    phone_number = models.CharField(max_length=15, blank=True, null=True)
    
    HOD_SCOPE_CHOICES = (
        ('FACILITY', 'HOD Fasiliti (Klinik/Hospital)'),
        ('STATE', 'Ketua Perkhidmatan Negeri'),
        ('NATIONAL', 'Ketua Perkhidmatan Kebangsaan'),
    )

    hod_scope = models.CharField(max_length=20, choices=HOD_SCOPE_CHOICES, default='FACILITY')
    assigned_state = models.CharField(max_length=50, blank=True, null=True)
    assigned_facility = models.CharField(max_length=150, blank=True, null=True)

    def __str__(self):
        return f"{self.user.first_name} {self.user.last_name} ({self.get_role_display()})"


# ==========================================
# 2. TUNJANG KLINIKAL (CLINICAL EXCELLENCE)
# ==========================================

# A. Modul: Clinical Delivery (CUSUM Log)
class ProcedureLog(models.Model):
    officer = models.ForeignKey(User, on_delete=models.CASCADE, related_name='procedure_logs')
    patient_ref = models.CharField(max_length=64, blank=True, null=True, db_index=True) # Hash SHA-256 sahaja
    clinical_domain = models.CharField(max_length=10, blank=True, null=True) # CTS / FMS
    facility = models.CharField(max_length=150, blank=True, null=True)
    procedure_name = models.CharField(max_length=150, default="LIMA-LAD Procedure")
    procedure_date = models.DateField(default=timezone.localdate)
    primary_diagnosis = models.CharField(max_length=200, blank=True, null=True)
    previous_intervention = models.CharField(max_length=200, blank=True, null=True)
    comorbidities = models.JSONField(default=list, blank=True) # Senarai komorbiditi
    lvef_percent = models.FloatField(blank=True, null=True)
    coronary_anatomy = models.CharField(max_length=100, blank=True, null=True)
    nyha_class = models.CharField(max_length=10, blank=True, null=True)
    duration_illness = models.CharField(max_length=50, blank=True, null=True)
    systolic_bp = models.PositiveSmallIntegerField(blank=True, null=True)
    diastolic_bp = models.PositiveSmallIntegerField(blank=True, null=True)
    hba1c_percent = models.FloatField(blank=True, null=True)
    ldl_mmol_l = models.FloatField(blank=True, null=True)

    # Renal Function & CKD Staging (NDR Aligned)
    serum_creatinine = models.FloatField(blank=True, null=True)
    egfr = models.FloatField(blank=True, null=True)
    ckd_stage = models.CharField(max_length=30, blank=True, null=True)
    urine_microalbumin = models.CharField(max_length=30, blank=True, null=True)

    # Annual Complication Screening & Organ Damage (NDR Aligned)
    visual_acuity = models.CharField(max_length=50, blank=True, null=True)
    fundus_result = models.CharField(max_length=50, blank=True, null=True)
    fundus_date = models.DateField(blank=True, null=True)
    foot_exam_risk = models.CharField(max_length=50, blank=True, null=True)
    foot_exam_date = models.DateField(blank=True, null=True)
    ecg_result = models.CharField(max_length=50, blank=True, null=True)
    ecg_date = models.DateField(blank=True, null=True)
    cxr_result = models.CharField(max_length=50, blank=True, null=True)
    cxr_date = models.DateField(blank=True, null=True)
    fatty_liver = models.CharField(max_length=30, blank=True, null=True)
    fatty_liver_date = models.DateField(blank=True, null=True)
    ed_screening = models.CharField(max_length=30, blank=True, null=True)
    dietitian_referral_date = models.DateField(blank=True, null=True)

    is_successful = models.BooleanField(default=True)
    complication_occurred = models.BooleanField(default=False)
    target_achieved = models.BooleanField(blank=True, null=True)
    score = models.FloatField(default=85.0) # Skor Prosedur (0-100)
    is_verified = models.BooleanField(default=False)
    hod_verifier = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='verified_procedures')
    verified_at = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f"{self.officer.username} - {self.procedure_name} ({self.procedure_date})"


# B. Modul: Adherence to Policies & Protocols
class PolicyDeclaration(models.Model):
    officer = models.ForeignKey(User, on_delete=models.CASCADE, related_name='policy_declarations')
    submitted_at = models.DateTimeField(auto_now_add=True)
    year = models.IntegerField(default=2026)
    
    # Skor 5 Domain (%)
    score_domain_a = models.FloatField(default=0.0) # Clinical Screening & Assessment
    score_domain_b = models.FloatField(default=0.0) # Risk Factors & Medication History
    score_domain_c = models.FloatField(default=0.0) # Referrals & Follow-up
    score_domain_d = models.FloatField(default=0.0) # Laboratory & Diagnostics
    score_domain_e = models.FloatField(default=0.0) # Treatment Targets & Preventive Care
    
    hod_remarks = models.TextField(blank=True, null=True)
    is_verified = models.BooleanField(default=False)

    def __str__(self):
        return f"Policy Declaration - {self.officer.username} ({self.year})"

class PolicyItemResponse(models.Model):
    declaration = models.ForeignKey(PolicyDeclaration, related_name='responses', on_delete=models.CASCADE)
    question_number = models.IntegerField() # 1 hingga 20
    is_compliant = models.BooleanField(default=False)
    compliance_ratio = models.FloatField(default=0.0) # 0.0 hingga 1.0
    selected_count = models.PositiveSmallIntegerField(blank=True, null=True)
    max_count = models.PositiveSmallIntegerField(blank=True, null=True)

    def __str__(self):
        return f"Q{self.question_number}: {'Compliant' if self.is_compliant else 'Non-Compliant'}"


# C. Modul: Documentation Monitoring (Pautan Terus Prosedur & Penjejak SLA 4 Jam)
class DocumentationLog(models.Model):
    officer = models.ForeignKey(User, on_delete=models.CASCADE, related_name='doc_logs')
    procedure = models.ForeignKey('ProcedureLog', on_delete=models.SET_NULL, null=True, blank=True, related_name='doc_logs')
    patient_id = models.CharField(max_length=100)
    encounter_type = models.CharField(max_length=150, blank=True, null=True)
    encounter_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    delay_hours = models.FloatField(default=0.0) # Masa diambil (jam)
    reason_for_delay = models.TextField(blank=True, null=True)
    delay_reason_category = models.CharField(max_length=100, blank=True, null=True)
    delay_reason_remarks = models.TextField(blank=True, null=True)
    is_ontime = models.BooleanField(default=True)
    is_completed = models.BooleanField(default=False) # True = Selesai Ditandatangani, False = Dalam Giliran (Pending)

    def __str__(self):
        return f"Doc Log - {self.officer.username} - Patient {self.patient_id}"


# ==========================================
# 3. KNOWLEDGE & CAPACITY (CPD / CME)
# ==========================================
class CPDClaim(models.Model):
    STATUS_CHOICES = (
        ('PENDING', 'Pending Verification'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
    )

    officer = models.ForeignKey(User, on_delete=models.CASCADE, related_name='cpd_claims')
    category = models.CharField(max_length=50, default="CME Workshop") # CME, Workshop, Quiz
    topic = models.CharField(max_length=200)
    points = models.IntegerField(default=10)
    event_date = models.DateField()
    end_date = models.DateField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    certificate = models.FileField(upload_to='cpd_certificates/%Y/%m/', blank=True, null=True)
    verified_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='verified_cpd_claims')
    verified_at = models.DateTimeField(blank=True, null=True)
    rejection_reason = models.TextField(blank=True, null=True)
    submitted_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"CPD ({self.points} pts) - {self.officer.username} - {self.topic}"


# ==========================================
# 4. TUNJANG OPERASIONAL (OPERATIONAL OUTPUT)
# ==========================================

# A. Modul: Productivity & Efficiency
class ProductivityLog(models.Model):
    officer = models.ForeignKey(User, on_delete=models.CASCADE, related_name='productivity_logs')
    record_date = models.DateField(default=timezone.localdate)
    patients_cleared = models.IntegerField(default=35)
    ward_cases = models.IntegerField(blank=True, null=True)
    procedures_completed = models.IntegerField(blank=True, null=True)
    avg_consult_minutes = models.FloatField(default=12.0)
    punctuality_rate = models.FloatField(default=95.0) # %
    punctuality_status = models.CharField(max_length=50, blank=True, null=True)
    operational_notes = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"Productivity {self.record_date} - {self.officer.username}"


# B. Modul: Innovation & Problem Solving
class InnovationProject(models.Model):
    officer = models.ForeignKey(User, on_delete=models.CASCADE, related_name='innovation_projects')
    project_title = models.CharField(max_length=200)
    innovation_type = models.CharField(max_length=50, blank=True, null=True) # Research, Clinical Trial, Technology, Publication
    category = models.CharField(max_length=100, default="System Compliance")
    research_role = models.CharField(max_length=100, blank=True, null=True)
    nmrr_id = models.CharField(max_length=50, blank=True, null=True)
    trial_phase = models.CharField(max_length=30, blank=True, null=True)
    recruitment_count = models.PositiveIntegerField(blank=True, null=True)
    trial_status = models.CharField(max_length=20, blank=True, null=True)
    adoption_date = models.DateField(blank=True, null=True)
    proficiency_level = models.CharField(max_length=50, blank=True, null=True)
    journal_name = models.CharField(max_length=200, blank=True, null=True)
    doi = models.CharField(max_length=100, blank=True, null=True)
    publication_year = models.PositiveIntegerField(blank=True, null=True)
    description = models.TextField()
    score = models.FloatField(default=80.0)
    is_verified = models.BooleanField(default=False)
    verified_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='verified_innovation_projects')
    verified_at = models.DateTimeField(blank=True, null=True)
    submitted_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Innovation: {self.project_title} - {self.officer.username}"


# ==========================================
# 5. TUNJANG INTERPERSONAL EXCELLENCE
# ==========================================

# A. Modul: Patient Satisfaction (MyPSQ Analytics)
class MyPSQFeedback(models.Model):
    doctor = models.ForeignKey(User, on_delete=models.CASCADE, related_name='mypsq_feedbacks')
    created_at = models.DateTimeField(auto_now_add=True)
    patient_category = models.CharField(max_length=50, blank=True, null=True) # Pesakit Luar / Dalam
    
    q1_general = models.IntegerField(default=5)      # Kepuasan Am
    q2_technical = models.IntegerField(default=5)    # Kualiti Teknikal
    q3_interpersonal = models.IntegerField(default=5)# Sikap Interpersonal
    q4_communication = models.IntegerField(default=5)# Komunikasi
    q5_financial = models.IntegerField(default=5)    # Aspek Kewangan
    q6_time = models.IntegerField(default=5)         # Masa Bersama Petugas
    q7_accessibility = models.IntegerField(default=5)# Akses & Keselesaan
    
    patient_comments = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"MyPSQ for {self.doctor.username} - {self.created_at.strftime('%Y-%m-%d')}"
        
    @property
    def average_score(self):
        total = (self.q1_general + self.q2_technical + self.q3_interpersonal + 
                 self.q4_communication + self.q5_financial + self.q6_time + self.q7_accessibility)
        return total / 7.0


# B. Modul: Stress Management (Emotional Intelligence)
class EmotionalReflection(models.Model):
    officer = models.ForeignKey(User, on_delete=models.CASCADE, related_name='emotional_reflections')
    record_date = models.DateField(default=timezone.localdate)
    self_awareness_score = models.IntegerField(default=85)
    self_regulation_score = models.IntegerField(blank=True, null=True)
    empathy_score = models.IntegerField(blank=True, null=True)
    deescalation_score = models.IntegerField(blank=True, null=True)
    trigger_event = models.CharField(max_length=200, blank=True, null=True)
    coping_strategy = models.CharField(max_length=200, blank=True, null=True)
    reflection_notes = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"Reflection {self.record_date} - {self.officer.username}"


# C. Modul: Teamwork & Collaboration (Peer Kudos)
class PeerKudos(models.Model):
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_kudos')
    receiver = models.ForeignKey(User, on_delete=models.CASCADE, related_name='received_kudos')
    category = models.CharField(max_length=50, default="General Clinical") # General Clinical, Procedural, Consultation
    points = models.IntegerField(default=5)
    message = models.TextField(blank=True, null=True)
    sent_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Kudos from {self.sender.username} to {self.receiver.username}"


# D. Modul: Training & Mentorship
class MentorshipSession(models.Model):
    ROLE_CHOICES = (
        ('mentor', 'As Mentor'),
        ('mentee', 'As Mentee'),
    )

    mentor = models.ForeignKey(User, on_delete=models.CASCADE, related_name='mentorship_sessions')
    participant_role = models.CharField(max_length=10, choices=ROLE_CHOICES, default='mentor')
    mentee_name = models.CharField(max_length=150)
    topic = models.CharField(max_length=200)
    notes = models.TextField(blank=True, null=True)
    session_date = models.DateField()
    duration_hours = models.FloatField(default=1.0)
    is_verified = models.BooleanField(default=True)
    verified_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='verified_mentorship_sessions')
    verified_at = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f"Mentorship by {self.mentor.username} for {self.mentee_name}"

# ==========================================
# 6. KOMUNIKASI DUA HALA (TWO-WAY COMMUNICATION)
# ==========================================
class HODFeedback(models.Model):
    SEVERITY_CHOICES = (
        ('INFO', 'General Info'),
        ('WARNING', 'Warning / Needs Attention'),
        ('URGENT', 'Urgent Action Required'),
        ('KUDOS', 'Praise / Commendation'),
    )
    
    sender_hod = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_feedbacks')
    receiver_staff = models.ForeignKey(User, on_delete=models.CASCADE, related_name='received_feedbacks')
    module_reference = models.CharField(max_length=100, default='General Dashboard') # Cth: Documentation, Policies
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES, default='WARNING')
    message_text = models.TextField()
    is_acknowledged = models.BooleanField(default=False)
    acknowledged_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"From {self.sender_hod.username} to {self.receiver_staff.username} ({self.severity})"