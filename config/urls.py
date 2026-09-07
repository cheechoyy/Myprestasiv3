from django.contrib import admin
from django.urls import path
from dashboard import views

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
    
    path('logout/', views.logout_view, name='logout'),
]