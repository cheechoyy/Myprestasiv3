from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path
from dashboard import views
from django.contrib.auth import views as auth_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.landing_page_view, name='home'),
    path('login/', views.login_view, name='login'),
    
    # --- TAMBAH BARIS INI UNTUK HPU ADMIN ---
    path('hpu-portal/', views.hpu_dashboard, name='hpu_dashboard'),
    
    path('staff/', views.staff_dashboard_view, name='staff_dashboard'), 
    path('hod-portal/', views.hod_dashboard, name='hod_dashboard'),
    path('cts-module/', views.cts_view, name='cts_module'),
    path('operational-module/', views.operational_view, name='operational_module'),
    path('interpersonal-module/', views.interpersonal_view, name='interpersonal_module'),
    
    # --- LALUAN BAHARU UNTUK DRILL-DOWN ANALISIS & PROFIL KOGNITIF ---
    path('overall-analytics/', views.overall_analytics_view, name='overall_analytics'),
    path('officer-profile/', views.officer_profile_view, name='officer_profile'),

    # --- LALUAN PENILAIAN PESAKIT MyPSQ (URL UNTUK KOD QR) ---
    # Membenarkan pesakit menilai terus menggunakan username/ID doktor
    path('evaluate/<str:doctor_username>/', views.patient_mypsq_view, name='patient_mypsq_form'),

    # --- 4 HALAMAN KHUSUS (DEDICATED INSIGHT VIEWS) ---
    path('insights/action-center/', views.action_center_insight_view, name='action_center_insight'),
    path('insights/kpi-analytics/', views.kpi_analytics_insight_view, name='kpi_analytics_insight'),
    path('insights/competency-matrix/', views.competency_matrix_insight_view, name='competency_matrix_insight'),
    path('insights/officers-directory/', views.officers_directory_insight_view, name='officers_directory_insight'),
    
    path('logout/', views.logout_view, name='logout'),
    path('register/', views.register_view, name='register'),
    
    # --- SISTEM FORGOT PASSWORD DJANGO ---
    path('password-reset/', auth_views.PasswordResetView.as_view(template_name='dashboard/password_reset.html'), name='password_reset'),
    path('password-reset/done/', auth_views.PasswordResetDoneView.as_view(template_name='dashboard/password_reset_done.html'), name='password_reset_done'),
]

if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )