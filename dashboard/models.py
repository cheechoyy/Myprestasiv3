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