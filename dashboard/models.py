from django.db import models
from django.contrib.auth.models import User

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
    procedure_name = models.CharField(max_length=150, default="LIMA-LAD Procedure")
    procedure_date = models.DateField(auto_now_add=True)
    is_successful = models.BooleanField(default=True)
    complication_occurred = models.BooleanField(default=False)
    score = models.FloatField(default=85.0) # Skor Prosedur (0-100)
    is_verified = models.BooleanField(default=False)
    hod_verifier = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='verified_procedures')

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

    def __str__(self):
        return f"Q{self.question_number}: {'Compliant' if self.is_compliant else 'Non-Compliant'}"


# C. Modul: Documentation Monitoring
class DocumentationLog(models.Model):
    officer = models.ForeignKey(User, on_delete=models.CASCADE, related_name='doc_logs')
    patient_id = models.CharField(max_length=50)
    created_at = models.DateTimeField(auto_now_add=True)
    delay_hours = models.FloatField(default=0.0) # Kelambatan entri
    reason_for_delay = models.TextField(blank=True, null=True)
    is_ontime = models.BooleanField(default=True)

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
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    submitted_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"CPD ({self.points} pts) - {self.officer.username} - {self.topic}"


# ==========================================
# 4. TUNJANG OPERASIONAL (OPERATIONAL OUTPUT)
# ==========================================

# A. Modul: Productivity & Efficiency
class ProductivityLog(models.Model):
    officer = models.ForeignKey(User, on_delete=models.CASCADE, related_name='productivity_logs')
    record_date = models.DateField(auto_now_add=True)
    patients_cleared = models.IntegerField(default=35)
    avg_consult_minutes = models.FloatField(default=12.0)
    punctuality_rate = models.FloatField(default=95.0) # %

    def __str__(self):
        return f"Productivity {self.record_date} - {self.officer.username}"


# B. Modul: Innovation & Problem Solving
class InnovationProject(models.Model):
    officer = models.ForeignKey(User, on_delete=models.CASCADE, related_name='innovation_projects')
    project_title = models.CharField(max_length=200)
    category = models.CharField(max_length=100, default="System Compliance")
    description = models.TextField()
    score = models.FloatField(default=80.0)
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
    record_date = models.DateField(auto_now_add=True)
    self_awareness_score = models.IntegerField(default=85)
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
    mentor = models.ForeignKey(User, on_delete=models.CASCADE, related_name='mentorship_sessions')
    mentee_name = models.CharField(max_length=150)
    topic = models.CharField(max_length=200)
    session_date = models.DateField()
    duration_hours = models.FloatField(default=1.0)
    is_verified = models.BooleanField(default=True)

    def __str__(self):
        return f"Mentorship by {self.mentor.username} for {self.mentee_name}"