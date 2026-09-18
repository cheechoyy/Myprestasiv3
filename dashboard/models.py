from django.db import models
from django.contrib.auth.models import User

class UserProfile(models.Model):
    # Pilihan Peranan (Role Choices)
    ROLE_CHOICES = (
        ('HPU_ADMIN', 'Super Admin (HQ)'),
        ('HOD', 'Ketua Jabatan / Pakar'),
        ('STAFF', 'Pegawai Perubatan (MO)'),
        ('AUDITOR', 'Pegawai Audit Kualiti'),
    )

    # Pilihan Jabatan (Department)
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

# --- MODEL BAHARU UNTUK MAKLUM BALAS MyPSQ ---
class MyPSQFeedback(models.Model):
    # Menyambungkan maklum balas ini dengan doktor (User) secara langsung
    doctor = models.ForeignKey(User, on_delete=models.CASCADE, related_name='mypsq_feedbacks')
    
    # Tarikh dan Masa dihantar
    created_at = models.DateTimeField(auto_now_add=True)
    
    # Maklumat Asas (Pilihan/Opsyenal untuk Privasi Pesakit)
    patient_category = models.CharField(max_length=50, blank=True, null=True) # Pesakit Luar / Dalam
    
    # 7 Soalan Teras MyPSQ (Skala 1 - 5)
    q1_general = models.IntegerField(default=5)      # Kepuasan Am
    q2_technical = models.IntegerField(default=5)    # Kualiti Teknikal
    q3_interpersonal = models.IntegerField(default=5)# Sikap Interpersonal
    q4_communication = models.IntegerField(default=5)# Komunikasi
    q5_financial = models.IntegerField(default=5)    # Aspek Kewangan
    q6_time = models.IntegerField(default=5)         # Masa Bersama Petugas
    q7_accessibility = models.IntegerField(default=5)# Akses & Keselesaan
    
    # Komen Tambahan
    patient_comments = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"MyPSQ for {self.doctor.username} - {self.created_at.strftime('%Y-%m-%d')}"
        
    @property
    def average_score(self):
        # Mengira purata keseluruhan bagi 7 soalan
        total = (self.q1_general + self.q2_technical + self.q3_interpersonal + 
                 self.q4_communication + self.q5_financial + self.q6_time + self.q7_accessibility)
        return total / 7.0