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
    path('admin-portal/', views.admin_dashboard, name='admin_dashboard'),
    path('cts-module/', views.cts_view, name='cts_module'),
    path('operational-module/', views.operational_view, name='operational_module'),
    path('interpersonal-module/', views.interpersonal_view, name='interpersonal_module'),
    path('logout/', views.logout_view, name='logout'),
]