from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth.models import User
from datetime import date, timedelta, datetime
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import folium
import random
import math
import hashlib
import re
import urllib.parse
import json
from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.core.validators import FileExtensionValidator
from django.db import transaction
from django.db.models import Avg
from django.utils import timezone

# Import Data Simulasi (Mock Data)
from .mock_data import STATE_CENTERS, MOCK_KLINIK, generate_mock_doctors, MOCK_CUSUM_DATA

# Import Enjin Pengira Skor & Model Pangkalan Data MyPrestasi
from .services import calculate_pri_score
from .models import (
    UserProfile, MyPSQFeedback, ProcedureLog, PolicyDeclaration,
    PolicyItemResponse, DocumentationLog, CPDClaim, ProductivityLog,
    InnovationProject, EmotionalReflection, PeerKudos, MentorshipSession,
    HODFeedback  # <--- IMPORT MODEL BARU INI
)


def landing_page_view(request):
    """
    Paparan Landing Page Awam (Tema Merah Pastel)
    """
    # Jika pengguna sudah log masuk, terus bawa ke portal masing-masing
    if request.user.is_authenticated or request.session.get('role') == 'staff':
        if request.session.get('role') == 'staff':
            return redirect('staff_dashboard')
        return redirect('hod_dashboard')
        
    return render(request, 'dashboard/landing.html')

# --- 2. LOGIK PENGURUSAN AKSES (LOGIN/LOGOUT DENGAN ROLE) ---
def home_redirect(request):
    return redirect('login')

def login_view(request):
    # Jika sudah log masuk, hantar ke portal masing-masing
    if request.user.is_authenticated:
        if request.user.is_superuser or getattr(request.user.profile, 'role', '') == 'HPU_ADMIN':
            return redirect('hpu_dashboard') # Portal Dev / Tech
        elif request.user.profile.role == 'HOD':
            return redirect('hod_dashboard') # Portal HOD
        elif request.user.profile.role == 'STAFF':
            return redirect('staff_dashboard') # Portal Staff
        elif request.user.profile.role == 'AUDITOR':
            return redirect('audit_dashboard') # Portal Audit

    if request.method == 'POST':
        user_ic = request.POST.get('username', '').strip()
        user_pass = request.POST.get('password', '').strip()

        # Sistem Django Auth akan menyemak di dalam db.sqlite3
        user = authenticate(request, username=user_ic, password=user_pass)
        
        if user is not None:
            login(request, user)
            
            # PENGHALAAN AUTOMATIK BERDASARKAN ROLE
            if user.is_superuser or getattr(user.profile, 'role', '') == 'HPU_ADMIN':
                request.session['role'] = 'admin_hpu'
                return redirect('hpu_dashboard')
                
            elif user.profile.role == 'HOD':
                request.session['role'] = 'admin'
                return redirect('hod_dashboard')
                
            elif user.profile.role == 'STAFF':
                request.session['role'] = 'staff'
                request.session['staff_dept'] = user.profile.department
                return redirect('staff_dashboard')
                
        else:
            messages.error(request, "❌ ID Pengguna atau Kata Laluan tidak sah.")

    return render(request, 'dashboard/login.html')

# Pastikan 'def' rapat ke dinding kiri!
def logout_view(request):
    logout(request)
    # Buang role dari session apabila log keluar
    if 'role' in request.session:
        del request.session['role']
    return redirect('login')

def register_view(request):
    """
    Paparan Pendaftaran Pengguna Baharu.
    Secara lalai (default), pengguna baru akan didaftarkan sebagai STAFF.
    Admin HPU boleh menukar role mereka di HPU Dashboard nanti.
    """
    # Jika sudah log masuk, tendang ke home
    if request.user.is_authenticated:
        return redirect('landing_page')

    if request.method == 'POST':
        user_ic = request.POST.get('username', '').strip() # Guna IC/ID sebagai username
        email = request.POST.get('email', '').strip()
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        department = request.POST.get('department', 'FMS')
        password = request.POST.get('password', '')
        confirm_password = request.POST.get('confirm_password', '')

        # Validasi asas
        if password != confirm_password:
            messages.error(request, "❌ Passwords do not match!")
            return redirect('register')
        
        if User.objects.filter(username=user_ic).exists():
            messages.error(request, "❌ User ID or IC Number is already registered.")
            return redirect('register')

        # 1. Cipta Akaun User (Bawaan Django)
        new_user = User.objects.create_user(username=user_ic, email=email, password=password)
        new_user.first_name = first_name
        new_user.last_name = last_name
        new_user.save()

        # 2. Cipta UserProfile dan sambungkan dengan DB MyPrestasi (Set as STAFF default)
        UserProfile.objects.create(
            user=new_user, 
            role='STAFF', 
            department=department
        )

        messages.success(request, "✅ Registration successful! Your account has been registered as Staff. Please login.")
        return redirect('login')

    return render(request, 'dashboard/register.html')


# ---LOGIK ADMIN DASHBOARD ---
@login_required(login_url='/login/')
def hod_dashboard(request):

    from django.contrib.auth import update_session_auth_hash

    # 1. TAMBAH BARIS INI: Tangkap isyarat tab (Default: Dashboard)
    section = request.GET.get('section', 'Dashboard')
    
    # 2. TAMBAH BARIS INI: Logik untuk paparkan mesej berjaya bila butang Save ditekan
    if request.method == 'POST':
        form_type = request.POST.get('form_type')
        
        if form_type == 'settings':
            user = request.user
            # Kemas kini maklumat asas
            full_name = request.POST.get('full_name', '').strip()
            if full_name:
                parts = full_name.split(' ', 1)
                user.first_name = parts[0]
                user.last_name = parts[1] if len(parts) > 1 else ''
            
            user.email = request.POST.get('email', '').strip()
            user.profile.phone_number = request.POST.get('phone', '').strip()
            user.save()
            user.profile.save()

            # Proses Penukaran Kata Laluan
            new_pass = request.POST.get('new_pass', '')
            confirm_pass = request.POST.get('confirm_pass', '')
            
            if new_pass:
                if new_pass == confirm_pass:
                    user.set_password(new_pass)
                    user.save()
                    update_session_auth_hash(request, user) # Elak auto-logout
                    messages.success(request, "✅ HOD Profile & Password successfully updated.")
                else:
                    messages.error(request, "❌ Passwords do not match!")
                    return redirect(request.path + '?section=Profile')
            else:
                messages.success(request, "✅ HOD Profile settings successfully updated.")
                
            return redirect(request.path + '?section=Profile')
        elif request.POST.get('form_type') == 'system_settings':
            # Simpan tetapan ke dalam Session Django
            request.session['dark_mode'] = (request.POST.get('dark_mode') == 'on')
            request.session['language'] = request.POST.get('language')
            request.session['timezone'] = request.POST.get('timezone')
            
            messages.success(request, "✅ System preferences successfully updated.")
            return redirect(request.path + '?section=Settings')
        
    # === LOGIK SKOP HOD ===
    hod_scope = getattr(request.user.profile, 'hod_scope', 'NATIONAL')
    hod_state = getattr(request.user.profile, 'assigned_state', None)
    hod_facility = getattr(request.user.profile, 'assigned_facility', None)

    # 1. Tapis Negeri Berdasarkan Skop
    if hod_scope in ['FACILITY', 'STATE'] and hod_state:
        pilihan_negeri = hod_state # Paksa negeri
    else:
        pilihan_negeri = request.GET.get('state', 'All States')

    if pilihan_negeri == "All States":
        klinik_dipaparkan = MOCK_KLINIK
    else:
        klinik_dipaparkan = [k for k in MOCK_KLINIK if k.get("state") == pilihan_negeri]

    senarai_klinik = [k['name'] for k in klinik_dipaparkan]
    
    # 2. Tapis Fasiliti Berdasarkan Skop
    if hod_scope == 'FACILITY' and hod_facility:
        pilihan_klinik = hod_facility # Paksa Fasiliti
        klinik_dipaparkan = [k for k in klinik_dipaparkan if k.get("name") == pilihan_klinik]
    else:
        pilihan_klinik = request.GET.get('clinic')
        if not pilihan_klinik or pilihan_klinik not in senarai_klinik:
            pilihan_klinik = senarai_klinik[0] if senarai_klinik else None
            
    # Hantar maklumat skop ke template
    is_facility_locked = (hod_scope == 'FACILITY')
    is_state_locked = (hod_scope == 'STATE')

    # --- INTEGRASI HYBRID DATA PIPELINE (REAL DB + MOCK FALLBACK) ---
    active_facilities = len(klinik_dipaparkan)
    semua_markah = []
    total_staff = 0

    # 1. Tarik Staf Sebenar dari Database
    real_staff_users = User.objects.filter(profile__role='STAFF')
    real_staff_data = []

    for staff_user in real_staff_users:
        scores = calculate_pri_score(staff_user)
        staff_facility = getattr(staff_user.profile, 'assigned_facility', '') or 'Klinik Kesihatan Medong'
        
        # Tapis mengikut klinik jika pilihan_klinik ditentukan
        if not pilihan_klinik or pilihan_klinik == 'All Clinics' or pilihan_klinik == staff_facility:
            full_name = f"{staff_user.first_name} {staff_user.last_name}".strip() or staff_user.username
            real_staff_data.append({
                'Officer Name': f"🟢 {full_name} (Live)",
                'Grade': 'UD43 (MO)',
                'Current Score': scores['pri_score'],
                'Status': f"<span class='status-badge status-excellent'>{scores['promotion_tier'].split(':')[0]}</span>"
            })
            semua_markah.append(scores['pri_score'])

    # 2. Ambil Data Mock untuk Melengkapkan Statistik Papan Pemuka
    for k in klinik_dipaparkan:
        if 'doctors' in k:
            total_staff += len(k['doctors'])
            semua_markah.extend([doc['Current Score'] for doc in k['doctors']])
            
    total_staff += len(real_staff_data)
    avg_performance = int(sum(semua_markah) / len(semua_markah)) if semua_markah else 0
    
    random.seed(total_staff or 1)
    evaluations_completed = total_staff * random.randint(3, 6)

    # A. Senarai Staf (Roster Hybrid)
    from django.urls import reverse
    
    if pilihan_klinik:
        df_docs = generate_mock_doctors(pilihan_klinik)
        
        # Gabungkan data Staf Sebenar di bahagian paling atas Roster
        if real_staff_data:
            df_real = pd.DataFrame(real_staff_data)
            df_docs = pd.concat([df_real, df_docs], ignore_index=True)
            
        clinical_url = reverse('cts_module')
        
        # Link terus ke borang penilaian pegawai
        df_docs['Officer Name'] = df_docs['Officer Name'].apply(
            lambda x: f"<a href='{clinical_url}?officer={urllib.parse.quote(x)}&clinic={urllib.parse.quote(pilihan_klinik)}&state={urllib.parse.quote(pilihan_negeri)}&tab=Skills' style='color:#6d3ec4; font-weight:800; text-decoration:none;' onmouseover='this.style.textDecoration=\"underline\"' onmouseout='this.style.textDecoration=\"none\"'>{x}</a>"
        )
        roster_html = df_docs.to_html(index=False, classes="roster-table", escape=False)
    else:
        df_docs = pd.DataFrame()
        roster_html = "<div style='text-align:center; padding: 20px; color:#a3aed1;'>No officers found for this state.</div>"

    # Dapatkan nama dinamik untuk header
    hod_name = f"{request.user.first_name} {request.user.last_name}".strip() or request.user.username
    hod_facility_label = hod_facility if hod_scope == 'FACILITY' and hod_facility else "HPU Putrajaya"

    # B. CARTA BAR KPI JABATAN (DATA SEBENAR DARI DB)
    kpi_categories = ["Clinical Excellence", "CPD & Knowledge", "Doc Audit", "Policy Compliance"]
    kpi_targets = [85, 80, 90, 100]
    
    # Nilai asal jika tiada data langsung
    actual_clinical, actual_cpd, actual_doc, actual_policy = 88, 75, 82, 95
    
    # Tapis pegawai berdasarkan klinik/fasiliti semasa HOD
    staff_for_kpi = User.objects.filter(profile__role='STAFF')
    if pilihan_klinik and pilihan_klinik != 'All Clinics':
        staff_for_kpi = staff_for_kpi.filter(profile__assigned_facility=pilihan_klinik)
        
    if staff_for_kpi.exists():
        # 1. Clinical Delivery (CUSUM Score Purata)
        proc_logs = ProcedureLog.objects.filter(officer__in=staff_for_kpi)
        if proc_logs.exists():
            actual_clinical = int(sum(p.score for p in proc_logs) / proc_logs.count())
            
        # 2. CPD & Knowledge (Andaikan sasaran 50 mata = 100%)
        cpd_claims = CPDClaim.objects.filter(officer__in=staff_for_kpi)
        if cpd_claims.exists():
            total_pts = sum(c.points for c in cpd_claims)
            actual_cpd = min(100, int((total_pts / (50.0 * staff_for_kpi.count())) * 100))
            
        # 3. Doc Audit (Peratusan Siap Tepat Masa)
        doc_logs = DocumentationLog.objects.filter(officer__in=staff_for_kpi)
        if doc_logs.exists():
            ontime_count = doc_logs.filter(is_ontime=True).count()
            actual_doc = int((ontime_count / doc_logs.count()) * 100)
            
        # 4. Policy Compliance (Purata keseluruhan 5 domain)
        policies = PolicyDeclaration.objects.filter(officer__in=staff_for_kpi)
        if policies.exists():
            total_policy = sum((p.score_domain_a + p.score_domain_b + p.score_domain_c + p.score_domain_d + p.score_domain_e)/5 for p in policies)
            actual_policy = int(total_policy / policies.count())

    kpi_actuals = [actual_clinical, actual_cpd, actual_doc, actual_policy]

    fig_kpi = go.Figure()
    fig_kpi.add_trace(go.Bar(
        x=kpi_categories, y=kpi_targets, name="Target KPI",
        marker=dict(color="#e7dff2", line=dict(color="#a577ff", width=1), cornerradius=8),
        text=[f"{v}%" for v in kpi_targets], textposition='auto',
        textfont=dict(color='#6d3ec4', size=11, family='Inter', weight='bold'),
        hovertemplate="<b>%{x}</b><br>Target: %{y}%<extra></extra>"
    ))
    fig_kpi.add_trace(go.Bar(
        x=kpi_categories, y=kpi_actuals, name="Actual Score",
        marker=dict(color="#6d3ec4", line=dict(color="#150a35", width=1), cornerradius=8),
        text=[f"{v}%" for v in kpi_actuals], textposition='auto',
        textfont=dict(color='#ffffff', size=11, family='Inter', weight='bold'),
        hovertemplate="<b>%{x}</b><br>Actual: %{y}%<extra></extra>"
    ))

    fig_kpi.update_layout(
        barmode='group', height=240,
        margin=dict(l=25, r=15, t=15, b=25), bargap=0.28, bargroupgap=0.12,
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(size=10, color='#150a35', family='Inter')),
        xaxis=dict(fixedrange=True, showgrid=False, tickfont=dict(size=9.5, color='#150a35', weight='bold', family='Inter')),
        yaxis=dict(fixedrange=True, showgrid=True, gridcolor='#e7dff2', range=[0, 110], tickfont=dict(size=9, color='#150a35'))
    )
    dept_kpi_html = fig_kpi.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False, 'responsive': True})

    # C. MO COMPETENCY HEATMAP (DATA SEBENAR DARI DB)
    mo_names = []
    z_mo_data = []
    
    # Ambil 5 Staf teratas untuk dipaparkan di Heatmap HOD
    top_staff = staff_for_kpi[:5]
    
    if top_staff.exists():
        for staff in top_staff:
            fname = f"Dr. {staff.first_name} {staff.last_name}".strip()
            mo_names.append(fname if fname != "Dr." else f"Dr. {staff.username}")
            
            # Gunakan Enjin MyPrestasi (calculate_pri_score) untuk tarik 3 Tunjang Utama!
            scores = calculate_pri_score(staff)
            z_mo_data.append([
                scores.get('score_clinical', 85),
                scores.get('score_operational', 80),
                scores.get('score_interpersonal', 88)
            ])
            
        # Fallback rawak jika staf sebenar kurang dari 5 orang supaya Heatmap tidak nampak kosong
        if len(mo_names) < 5 and not df_docs.empty:
            mock_names = df_docs['Officer Name'].str.replace(r'<[^>]*>', '', regex=True).tolist()
            for name in mock_names:
                if name not in mo_names and len(mo_names) < 5:
                    mo_names.append(name)
                    z_mo_data.append([random.randint(70,95), random.randint(70,95), random.randint(70,95)])
    else:
        # Fallback jika pangkalan data 100% kosong
        if not df_docs.empty:
            mo_names = df_docs['Officer Name'].str.replace(r'<[^>]*>', '', regex=True).tolist()[:5]
        else:
            mo_names = ["Dr. Ramesh Lee", "Dr. Siti Abu", "Dr. Chong Ali", "Dr. Mei Ismail", "Dr. Mohammad"]

        z_mo_data = [
            [88, 92, 78], [65, 70, 82], [94, 85, 90], [72, 68, 75], [85, 88, 92]
        ]
        
    tunjang_labels = ['Clinical', 'Operational', 'Interpersonal']

    fig_mo_heat = go.Figure(data=go.Heatmap(
        z=z_mo_data, x=tunjang_labels, y=mo_names,
        colorscale=[[0, '#f3edff'], [0.4, '#d8c7f4'], [0.7, '#a577ff'], [1.0, '#6d3ec4']],
        texttemplate="<b>%{z}%</b>", textfont=dict(size=12, color="#ffffff", family='Inter'),
        showscale=False, xgap=6, ygap=6,
        hovertemplate="<b>Pegawai:</b> %{y}<br><b>Tunjang:</b> %{x}<br><b>Skor:</b> %{z}%<extra></extra>"
    ))
    fig_mo_heat.update_layout(
        autosize=True, height=250,
        margin=dict(l=10, r=10, t=10, b=25),
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
        xaxis=dict(fixedrange=True, tickfont=dict(size=10, color='#150a35', weight='bold', family='Inter')),
        yaxis=dict(showticklabels=False, fixedrange=True, autorange="reversed")
    )
    mo_heatmap_html = fig_mo_heat.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False, 'responsive': True})

    # D. MODERN RADIAL SUNBURST (CENTER ROOT LABEL & PURPLE COLORWAYS)
    sunburst_ids = [
        "root",
        "clinical", "operational", "interpersonal",
        "skills", "knowledge", "policies", "documentation",
        "productivity", "innovation",
        "patient", "ei", "teamwork", "mentorship"
    ]
    sunburst_labels = [
        "<span style='color:#150a35; font-size:11px; font-weight:900;'>FRAMEWORK<br><span style='color:#6d3ec4; font-size:9px; font-weight:700;'>MAP</span><br><span style='color:#a577ff; font-size:8px; font-weight:600;'>[ ↺ RESET ]</span></span>",
        "Clinical<br>Excellence", "Operational", "Interpersonal<br>Excellence",
        "Skills", "Knowledge &<br>Capacity", "Policies &<br>Protocols", "Documentation",
        "Productivity &<br>Efficiency", "Innovation &<br>Problem Solving",
        "Patient<br>Experience", "Emotional<br>Intelligence", "Teamwork &<br>Collaboration", "Mentorship"
    ]
    sunburst_parents = [
        "",
        "root", "root", "root",
        "clinical", "clinical", "clinical", "clinical",
        "operational", "operational",
        "interpersonal", "interpersonal", "interpersonal", "interpersonal"
    ]
    sunburst_colors = [
        "#ffffff",                      # Center Core 
        "#150a35", "#6d3ec4", "#5528a6", # Tunjang Utama (Level 1)
        "#201243", "#3b1e70", "#5528a6", "#6d3ec4", # Sub-modul Klinikal (Level 2)
        "#8554d4", "#a577ff",                      # Sub-modul Operasional (Level 2)
        "#6d3ec4", "#8554d4", "#a577ff", "#c8b6e8"  # Sub-modul Interpersonal (Level 2)
    ]

    fig_sunburst = go.Figure(go.Sunburst(
        ids=sunburst_ids,
        labels=sunburst_labels,
        parents=sunburst_parents,
        marker=dict(
            colors=sunburst_colors,
            line=dict(color="#ffffff", width=2.5)
        ),
        insidetextorientation='horizontal',
        textfont=dict(size=9.5, color='#ffffff', family='Inter', weight='bold'),
        hovertemplate="<b>%{label}</b><br><span style='font-size:10px; color:#f3edff;'>Click to Zoom or Navigate</span><extra></extra>"
    ))

    fig_sunburst.update_layout(
        height=350, width=350,
        margin=dict(t=0, l=0, r=0, b=0),
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        autosize=False,
        clickmode='event'
    )
    sunburst_html_raw = fig_sunburst.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False})
    
    # Skrip JavaScript khas untuk menangkap acara klik Plotly dan redirect ke URL modul
    js_click_handler = """
    <script>
        document.addEventListener("DOMContentLoaded", function() {
            var plotElements = document.querySelectorAll('.js-plotly-plot');
            plotElements.forEach(function(plotElement) {
                if(plotElement.closest('.orbital-center-hub')) {
                    plotElement.on('plotly_sunburstclick', function(data) {
                        if (data.points && data.points.length > 0) {
                            var clicked_id = data.points[0].id.toLowerCase();
                            var dept_val = "%s";
                            var state_val = "%s";
                            var clinic_val = "%s";
                            
                            // Logik Pautan Terus: HANYA 10 SUB-MODUL (Dan terus buka tab spesifik)
                            var targetUrl = "";
                            
                            // 1. Modul Klinikal (4 Sub)
                            if (clicked_id === 'skills') targetUrl = "/cts-module/?tab=Skills";
                            else if (clicked_id === 'knowledge') targetUrl = "/cts-module/?tab=Knowledge";
                            else if (clicked_id === 'documentation') targetUrl = "/cts-module/?tab=Documentation";
                            else if (clicked_id === 'policies') targetUrl = "/cts-module/?tab=Policies";
                            
                            // 2. Modul Operasional (2 Sub)
                            else if (clicked_id === 'productivity') targetUrl = "/operational-module/?tab=Productivity";
                            else if (clicked_id === 'innovation') targetUrl = "/operational-module/?tab=Innovation";
                            
                            // 3. Modul Interpersonal (4 Sub)
                            else if (clicked_id === 'patient') targetUrl = "/interpersonal-module/?tab=Patient";
                            else if (clicked_id === 'ei') targetUrl = "/interpersonal-module/?tab=EI";
                            else if (clicked_id === 'teamwork') targetUrl = "/interpersonal-module/?tab=Teamwork";
                            else if (clicked_id === 'mentorship') targetUrl = "/interpersonal-module/?tab=Mentorship";

                            // Lakukan redirect hanya jika salah satu dari 10 sub-modul ini ditekan
                            if (targetUrl !== "") {
                                window.location.href = targetUrl + "&dept=" + dept_val + "&state=" + state_val + "&clinic=" + clinic_val;
                            }
                            // Jika 3 Tunjang utama diklik, kod ini diabaikan dan carta hanya akan di-zoom.
                        }
                    });
                }
            });
        });
    </script>
    """ % (getattr(request.user.profile, 'department', 'FMS'), pilihan_negeri, pilihan_klinik)
    
    sunburst_html = sunburst_html_raw + js_click_handler

    # --- JANA DATA KALENDAR DINAMIK HOD DARI DB SEBENAR ---
    hod_calendar_events = []
    
    # 1. Tuntutan CPD Menunggu Pengesahan HOD
    for cpd in CPDClaim.objects.filter(status='PENDING'):
        ev_day = cpd.submitted_at.day if hasattr(cpd, 'submitted_at') and cpd.submitted_at else date.today().day
        ev_date = cpd.submitted_at.strftime('%Y-%m-%d') if hasattr(cpd, 'submitted_at') and cpd.submitted_at else date.today().strftime('%Y-%m-%d')
        c_name = f"{cpd.officer.first_name} {cpd.officer.last_name}".strip() or cpd.officer.username
        hod_calendar_events.append({
            'title': f"CPD Claim: Dr. {c_name} ({cpd.points} Pts)",
            'date': ev_date,
            'day': ev_day,
            'type': 'pending',
            'desc': f"Topic: {cpd.topic}"
        })

    # 2. Amaran Dokumentasi Lewat dari Staf
    for ddoc in DocumentationLog.objects.filter(is_ontime=False)[:4]:
        ev_day = ddoc.created_at.day if hasattr(ddoc, 'created_at') and ddoc.created_at else date.today().day
        ev_date = ddoc.created_at.strftime('%Y-%m-%d') if hasattr(ddoc, 'created_at') and ddoc.created_at else date.today().strftime('%Y-%m-%d')
        d_name = f"{ddoc.officer.first_name} {ddoc.officer.last_name}".strip() or ddoc.officer.username
        hod_calendar_events.append({
            'title': f"Late Doc Audit: Dr. {d_name}",
            'date': ev_date,
            'day': ev_day,
            'type': 'duty',
            'desc': f"Patient ID: {ddoc.patient_id} ({ddoc.delay_hours}h delay)"
        })

    # 3. Pegawai Berpotensi Fast-Track (PRI >= 85)
    for staff_u in User.objects.filter(profile__role='STAFF'):
        sc = calculate_pri_score(staff_u)
        if sc.get('pri_score', 0) >= 85:
            st_name = f"{staff_u.first_name} {staff_u.last_name}".strip() or staff_u.username
            hod_calendar_events.append({
                'title': f"Fast-Track Review: Dr. {st_name}",
                'date': date.today().strftime('%Y-%m-%d'),
                'day': date.today().day,
                'type': 'fast-track',
                'desc': f"PRI Score: {sc.get('pri_score')}% ({sc.get('next_grade')})"
            })
            break

    calendar_events_json = json.dumps(hod_calendar_events, cls=DjangoJSONEncoder)

    context = {
        'section': section,
        'calendar_events_json': calendar_events_json,
        'roster_html': roster_html,
        'dept_kpi_html': dept_kpi_html,
        'mo_heatmap_html': mo_heatmap_html,
        'sunburst_html': sunburst_html,
        'senarai_negeri': STATE_CENTERS.keys(),
        'pilihan_negeri': pilihan_negeri,
        'senarai_klinik': senarai_klinik,
        'pilihan_klinik': pilihan_klinik,
        'is_facility_locked': is_facility_locked,
        'is_state_locked': is_state_locked,
        'hod_scope': hod_scope,

        'hod_name': hod_name,
        'hod_facility_label': hod_facility_label,
        'section': section,
        
        # --- DATA DINAMIK OVERVIEW STATISTIK ---
        'active_facilities': active_facilities,
        'total_staff': total_staff,
        'avg_performance': avg_performance,
        'evaluations_completed': evaluations_completed,

        # --- DATA DINAMIK HOD DASHBOARD ---
        'actual_clinical': actual_clinical,
        'actual_cpd': actual_cpd,
        'actual_doc': actual_doc,
        'actual_policy': actual_policy,
        'mo_names': mo_names,
        'z_mo_data': z_mo_data,
    }
    return render(request, 'dashboard/hod_dashboard.html', context)

# --- LOGIK MODUL KLINIKAL (CTS) LENGKAP ---
@login_required(login_url='/login/')
def cts_view(request):
    
    # --- LOGIK HANTAR FEEDBACK DUA HALA (HOD -> STAFF) ---
    if request.method == 'POST' and request.POST.get('form_type') == 'send_hod_feedback':
        target_username = request.POST.get('target_username', '').strip()
        message_content = request.POST.get('message_text', '').strip()
        mod_ref = request.POST.get('module_ref', 'Clinical Excellence')
        severity = request.POST.get('severity', 'WARNING')
        
        # Bersihkan label "🟢", "Dr.", dan "(Live)" untuk mendapatkan username asal
        clean_target = target_username.replace("🟢", "").replace("Dr.", "").split(" (")[0].strip()
        
        target_user = User.objects.filter(username__icontains=clean_target).first()
        if not target_user:
            target_user = User.objects.filter(first_name__icontains=clean_target).first()
            
        if target_user and message_content:
            HODFeedback.objects.create(
                sender_hod=request.user,
                receiver_staff=target_user,
                module_reference=mod_ref,
                severity=severity,
                message_text=message_content
            )
            messages.success(request, f"✅ Feedback sent successfully to Dr. {target_user.first_name} {target_user.last_name}.")
        else:
            messages.error(request, "❌ Failed to send feedback. Officer not found or message empty.")
            
        return redirect(request.get_full_path())

    sub_module = request.GET.get('sub_module', 'Clinical')
    active_tab = request.GET.get('tab', 'Skills')
    
    # Utamakan Jabatan dari Profil Pengguna berbanding parameter URL
    if hasattr(request.user, 'profile') and request.user.profile.department:
        dept = request.user.profile.department
    else:
        dept = request.GET.get('dept', 'FMS')

    # === LOGIK SKOP HOD ===
    hod_scope = getattr(request.user.profile, 'hod_scope', 'NATIONAL')
    hod_state = getattr(request.user.profile, 'assigned_state', None)
    hod_facility = getattr(request.user.profile, 'assigned_facility', None)

    # --- 1. TAPIS NEGERI ---
    senarai_negeri = list(STATE_CENTERS.keys())
    if hod_scope in ['FACILITY', 'STATE'] and hod_state:
        pilihan_negeri = hod_state
    else:
        pilihan_negeri = request.GET.get('state', 'All States')

    # --- 2. TAPIS KLINIK ---
    if pilihan_negeri == "All States":
        klinik_tersedia = MOCK_KLINIK
    else:
        klinik_tersedia = [k for k in MOCK_KLINIK if k["state"] == pilihan_negeri]

    nama_klinik_list = [k["name"] for k in klinik_tersedia]
    senarai_klinik = ["All Clinics"] + nama_klinik_list

    if hod_scope == 'FACILITY' and hod_facility:
        pilihan_klinik = hod_facility
        # Tapis supaya senarai doktor hanya dari hospital ini
        klinik_tersedia = [k for k in klinik_tersedia if k["name"] == pilihan_klinik]
    else:
        pilihan_klinik = request.GET.get('clinic', 'All Clinics')
        if pilihan_klinik not in senarai_klinik:
            pilihan_klinik = 'All Clinics'
            
    is_facility_locked = (hod_scope == 'FACILITY')
    is_state_locked = (hod_scope == 'STATE')
    
    # Nama dan fasiliti HOD untuk Header
    hod_name = f"{request.user.first_name} {request.user.last_name}".strip()
    if not hod_name:
        hod_name = request.user.username
    hod_facility_label = hod_facility if hod_scope == 'FACILITY' and hod_facility else "HPU Putrajaya"

    from django.db.models import Q

    # --- 3. TAPIS DOKTOR & KIRA STATISTIK (SEBENAR + MOCK) ---
    senarai_doktor = []
    semua_markah = [] 

    # A. Tarik Staf Sebenar dari SQLite Database
    real_staff = User.objects.filter(profile__role='STAFF')
    
    # Tapisan 1: Jabatan (Tebaan anda tepat! Asingkan staf FMS dan CTS)
    if dept:
        real_staff = real_staff.filter(profile__department=dept)
        
    # Tapisan 2: Fasiliti (Guna 'Q' untuk benarkan akaun baru yang belum ada fasiliti masuk ke senarai)
    if pilihan_klinik and pilihan_klinik != "All Clinics":
        real_staff = real_staff.filter(
            Q(profile__assigned_facility=pilihan_klinik) | 
            Q(profile__assigned_facility__isnull=True) | 
            Q(profile__assigned_facility__exact='')
        )

    for s in real_staff:
        full_name = f"{s.first_name} {s.last_name}".strip()
        display_name = f"🟢 Dr. {full_name} (Live)" if full_name else f"🟢 Dr. {s.username} (Live)"
        senarai_doktor.append(display_name)
        scores = calculate_pri_score(s)
        semua_markah.append(scores.get('pri_score', 85))

    # B. Tarik Mock Doctors sebagai Tambahan
    if pilihan_klinik == "All Clinics":
        for k in klinik_tersedia:
            df_docs = generate_mock_doctors(k['name'])
            senarai_doktor.extend(df_docs['Officer Name'].tolist())
            semua_markah.extend(df_docs['Current Score'].tolist())
    else:
        df_docs = generate_mock_doctors(pilihan_klinik)
        senarai_doktor.extend(df_docs['Officer Name'].tolist())
        semua_markah.extend(df_docs['Current Score'].tolist())

    # Buang duplikasi dengan mengekalkan staf sebenar di kedudukan teratas
    senarai_doktor = list(dict.fromkeys(senarai_doktor))

    pilihan_doktor = request.GET.get('officer')
    if not pilihan_doktor or pilihan_doktor not in senarai_doktor:
        pilihan_doktor = senarai_doktor[0] if senarai_doktor else "Tiada Pegawai"

    # --- PENGIRAAN STATISTIK DINAMIK ---
    active_facilities = len(klinik_tersedia) if pilihan_klinik == "All Clinics" else 1
    total_staff = len(senarai_doktor)
    avg_performance = int(sum(semua_markah) / len(semua_markah)) if semua_markah else 0
    
    # Jana jumlah penilaian rawak berdasarkan bilangan staf
    import random
    random.seed(total_staff)
    evaluations_completed = total_staff * random.randint(3, 6)

    # Pembolehubah Carta (Perlu diisytiharkan di luar blok if/elif)
    chart_html = ""
    gauge_score_html = ""
    gauge_peer_html = ""
    time_chart_html = ""
    history_html = ""
    delayed_html = ""
    
    # TAMBAHKAN/GANTIKAN DENGAN BARIS INI:
    cpd_text = ""
    cpd_css_class = ""
    peer_text = ""
    peer_css_class = ""

    # ==========================================
    # TAB 1: SKILLS (CUSUM)
    # ==========================================
    if active_tab == 'Skills':
        # Dapatkan nama tanpa nombor ID (Contoh: "Dr. Ah Meng (401)" -> "Dr. Ah Meng")
        nama_asas = pilihan_doktor.split(" (")[0].strip() if " (" in pilihan_doktor else pilihan_doktor.strip()

        fig = go.Figure()

        if dept == 'FMS':
            # ==========================================
            # ANALISIS LONGITUDINAL (KAPLAN-MEIER & GEE TREN) - ENGLISH
            # ==========================================
        
           # Ambil pilihan Quarter dari URL (Default: Q8 2025 jika baru buka)
            pilihan_quarter = request.GET.get('quarter', 'Q8')
            
            # Pembolehubah awal (Default)
            fms_total_patients, fms_kadar_kawalan = 0, 0
            fms_p_value, fms_prestasi = "", ""
            chart_km_html, chart_gee_html = "", ""
            box_bg, box_border, box_text = "#f8f9fa", "#e2e8f0", "#2b3674"
            
            # 1. Jana Variasi Data Berdasarkan Nama Doktor + QUARTER PILIHAN
            # Ini memastikan data berubah setiap kali HOD tukar Quarter!
            seed_str = str(pilihan_doktor) + str(pilihan_quarter) + "FMS_GEE_UNIK"
            seed_val = int(hashlib.md5(seed_str.encode('utf-8')).hexdigest(), 16) % (2**32 - 1)
            np.random.seed(seed_val)
            
            # Hasilkan Skor Kemahiran unik untuk gabungan Doktor + Quarter
            doc_skill = np.random.uniform(0, 100)
            
            if doc_skill >= 66:
                prestasi = 'Excellent'
                box_bg, box_border, box_text = '#f3edff', '#6d3ec4', '#150a35'
                base_drop = np.random.uniform(0.6, 0.9) 
                fms_p_value = "< 0.001"
            elif doc_skill >= 33:
                prestasi = 'Moderate'
                box_bg, box_border, box_text = '#f3edff', '#6d3ec4', '#150a35'
                base_drop = np.random.uniform(0.2, 0.45) 
                fms_p_value = f"0.0{np.random.randint(11, 49)}" 
            else:
                prestasi = 'Needs Improvement'
                box_bg, box_border, box_text = '#e7dff2', '#6d3ec4', '#150a35' 
                base_drop = np.random.uniform(-0.1, 0.1) 
                fms_p_value = f"0.{np.random.randint(150, 600)}" 

            n_pesakit = np.random.randint(50, 100)
            bulan = [0, 3, 6, 9, 12] 
            
            time_noise = {3: np.random.uniform(-0.2, 0.2), 6: np.random.uniform(-0.2, 0.2), 
                          9: np.random.uniform(-0.2, 0.2), 12: np.random.uniform(-0.2, 0.2)}

            data = []
            capai_target_count = 0
            
            for p in range(1, n_pesakit + 1):
                hba1c = np.random.normal(9.5, 0.8) 
                target_achieved = 0

                for b in bulan:
                    if b > 0:
                        drop = np.random.normal(base_drop, 0.2) 
                        hba1c = hba1c - drop + time_noise[b] 
                    
                    status_capai = 1 if hba1c < 7.0 else 0
                    data.append({'Patient_ID': p, 'Bulan': b, 'HbA1c': max(4.5, hba1c), 'Target_Achieved': status_capai})
                    
                    if status_capai == 1 and target_achieved == 0:
                        target_achieved = 1
                        
                if target_achieved == 1:
                    capai_target_count += 1
                    
            df = pd.DataFrame(data)
            
            # 2. Pengiraan Metrik Kad Laporan
            fms_kadar_kawalan = round((capai_target_count / n_pesakit) * 100, 1)
            fms_total_patients = n_pesakit
            fms_prestasi = prestasi
            
            # 2. Pengiraan Metrik Kad Laporan
            fms_kadar_kawalan = round((capai_target_count / n_pesakit) * 100, 1)
            fms_total_patients = n_pesakit
            fms_prestasi = prestasi

            # 3. LUKIS GRAF 1: KAPLAN-MEIER
            km_x = [0, 3, 6, 9, 12]
            if prestasi == 'Excellent': km_y = [0, 0.20, 0.55, 0.80, 0.92]
            elif prestasi == 'Moderate': km_y = [0, 0.08, 0.30, 0.55, 0.70]
            else: km_y = [0, 0.02, 0.08, 0.15, 0.22]
            
            km_y = [min(1.0, max(0.0, y + np.random.uniform(-0.03, 0.03))) if x > 0 else 0 for x, y in zip(km_x, km_y)]

            fig_km = go.Figure()
            fig_km.add_trace(go.Scatter(
                x=km_x, y=km_y, mode='lines+markers', line_shape='hv',
                line=dict(color='#150a35', width=3), marker=dict(size=8, color='#6d3ec4'),
                name='Achievement Probability'
            ))
            fig_km.update_layout(
                title=dict(text="<b>Kaplan-Meier Survival Analysis</b><br><span style='font-size:11px; color:#a577ff;'>Probability of Achieving Target HbA1c < 7.0% Over Time</span>", font=dict(size=13, color="#150a35")),
                xaxis_title="Monitoring Time (Months)", yaxis_title="Probability",
                yaxis=dict(tickformat='.0%', range=[0, 1.05], gridcolor='#e7dff2'), xaxis=dict(gridcolor='#e7dff2'), height=300,
                margin=dict(t=50, l=40, r=20, b=40), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)'
            )
            chart_km_html = fig_km.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False})

            # 4. LUKIS GRAF 2: TREND GEE (SPAGHETTI PLOT + MARGINAL MEAN)
            df_agg = df.groupby('Bulan')['HbA1c'].agg(['mean', 'std']).reset_index()
            # Simulasi purata klinik yang statik / naik sikit
            clinic_avg = [9.5, 9.3 + np.random.uniform(-0.1,0.1), 9.0 + np.random.uniform(-0.1,0.1), 8.7 + np.random.uniform(-0.1,0.1), 8.4 + np.random.uniform(-0.1,0.1)] 
            
            fig_gee = go.Figure()
            
            # A. LUKIS SPAGHETTI PLOT (Latar Belakang - 15 Pesakit Rawak)
            # Ini yang membezakan GEE dengan graf line biasa!
            sample_patients = df['Patient_ID'].unique()[:15] 
            for pid in sample_patients:
                p_data = df[df['Patient_ID'] == pid]
                fig_gee.add_trace(go.Scatter(
                    x=p_data['Bulan'], y=p_data['HbA1c'], mode='lines',
                    line=dict(color='gray', width=1), opacity=0.2,
                    showlegend=False, hoverinfo='skip'
                ))
            
            # B. LUKIS GARISAN GEE (Purata Marginal / Estimated Marginal Means)
            line_color = '#150a35' if prestasi == 'Excellent' else ('#6d3ec4' if prestasi == 'Moderate' else '#fb7185')
            fig_gee.add_trace(go.Scatter(
                x=df_agg['Bulan'], y=df_agg['mean'], mode='lines+markers',
                line=dict(color=line_color, width=4), marker=dict(size=10),
                error_y=dict(type='data', array=df_agg['std']/3, visible=True, color=line_color, thickness=2),
                name=f"GEE Marginal Mean"
            ))
            
            # C. LUKIS GARISAN PURATA KLINIK
            fig_gee.add_trace(go.Scatter(
                x=df_agg['Bulan'], y=clinic_avg, mode='lines+markers',
                line=dict(color='#9f1239', width=2, dash='dot'), marker=dict(size=6),
                name="Clinic Benchmark"
            ))
            
            fig_gee.add_hline(y=7.0, line_dash="solid", line_color="#6d3ec4", line_width=1.5, annotation_text="Target (<7.0%)")
            
            fig_gee.update_layout(
                title=dict(text="<b>Longitudinal Trend (GEE Model)</b><br><span style='font-size:11px; color:#a577ff;'>Individual Trajectories (Spaghetti) overlayed with Population-Averaged Mean</span>", font=dict(size=13, color="#150a35")),
                xaxis_title="Visit (Months)", yaxis_title="Mean HbA1c (%)",
                height=300, margin=dict(t=50, l=40, r=20, b=40),
                paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                yaxis=dict(range=[5.0, 12.0], gridcolor='#e7dff2'), xaxis=dict(gridcolor='#e7dff2'),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(size=10, color='#150a35'))
            )
            chart_gee_html = fig_gee.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False})

       # ==========================================
        # TAMBAH BLOK 'ELSE' INI UNTUK CTS CUSUM LIMA-LAD
        # ==========================================
        else:
            import random
            
            # KUNCI UTAMA: Jadikan nama doktor sebagai 'seed'. 
            # Ini menjamin setiap doktor dapat graf unik, tapi graf itu kekal tak berubah bila di-refresh!
            random.seed(pilihan_doktor)
            
            actual_scores = []
            # Tentukan jenis 'skill' doktor secara rawak untuk mempelbagaikan bentuk graf
            kategori_skill = random.choice(["Pakar", "Sederhana", "Lemah Awal"])
            
            if kategori_skill == "Pakar":
                base_score = random.randint(75, 85)
            elif kategori_skill == "Lemah Awal":
                base_score = random.randint(60, 70)
            else:
                base_score = random.randint(68, 75)
                
            # Jana 20 markah prosedur (LIMA-LAD)
            for i in range(20):
                if kategori_skill == "Pakar":
                    base_score += random.randint(-1, 3) # Pakar cepat capai 100
                elif kategori_skill == "Lemah Awal":
                    base_score += random.randint(0, 4)  # Mula rendah, tapi belajar mendadak
                else:
                    base_score += random.randint(0, 2)
                    
                base_score = min(100, max(0, base_score)) # Limit 0-100
                actual_scores.append(base_score)

            target_score = 80
            procedures = list(range(1, 21))
            cusum_values = []
            current_cusum = 0
            
            # Kira variasi CUSUM
            for score in actual_scores:
                variation = score - target_score
                current_cusum += variation
                cusum_values.append(current_cusum)

            fig.add_trace(go.Scatter(
                x=procedures, y=cusum_values, mode='lines+markers',
                name='CUSUM', line=dict(color='#150a35', width=3), marker=dict(size=8, color='#6d3ec4')
            ))
            
            fig.add_hline(y=0, line_dash="dash", line_color="#fecdd3", annotation_text="Baseline KPI (80)")
            fig.update_layout(
                title=dict(text=f"<b>Analisis CUSUM: {pilihan_doktor}</b>", font=dict(size=13, color="#150a35")),
                margin=dict(t=35, l=0, r=0, b=0), 
                height=250, 
                paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                xaxis_title="Number of LIMA-LAD Procedures", yaxis_title="CUSUM Variation",
                xaxis=dict(tickmode='linear', tick0=1, dtick=1, gridcolor='#e7dff2'), yaxis=dict(gridcolor='#e7dff2')
            )
            
            chart_html = fig.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False})

    # ==========================================
    # TAB 2: KNOWLEDGE (GAUGES & TABLE)
    # ==========================================
    elif active_tab == 'Knowledge':
        # 1. Dapatkan markah sebenar dari Database (Asumsi 50 mata = 100%)
        doc_username_clean = pilihan_doktor.replace("🟢", "").replace("Dr.", "").split(" (")[0].strip()
        target_user = User.objects.filter(username__icontains=doc_username_clean).first()
        if not target_user:
            target_user = User.objects.filter(first_name__icontains=doc_username_clean).first()

        cpd_score = 0
        history_df = pd.DataFrame(columns=["Date", "Category", "Topic", "Marks"])

        if target_user:
            cpd_claims = CPDClaim.objects.filter(officer=target_user)
            if cpd_claims.exists():
                # Hanya kira mata dari status APPROVED
                total_pts = sum(c.points for c in cpd_claims if c.status == 'APPROVED')
                cpd_score = min(100, int((total_pts / 50.0) * 100))
                
                # Bina Jadual Sejarah (5 terkini)
                history_data = []
                for claim in cpd_claims.order_by('-event_date')[:5]:
                    history_data.append({
                        "Date": claim.event_date.strftime("%d/%m/%Y"),
                        "Category": claim.category,
                        "Topic": claim.topic,
                        "Marks": f"{claim.points} pts ({claim.status})"
                    })
                history_df = pd.DataFrame(history_data)

        # 2. Peer average ambil dari purata sebenar klinik
        peer_score = avg_performance if avg_performance > 0 else 75

        # 3. Fungsi status automatik
        def get_status_info(score):
            if score >= 85: return "Outstanding", "outstanding"
            elif score >= 75: return "Good", "good"
            elif score >= 65: return "Competent", "competent"
            else: return "Needs Attention", "needs-attention"

        cpd_text, cpd_css_class = get_status_info(cpd_score)
        peer_text, peer_css_class = get_status_info(peer_score)

        # GAUGE DOKTOR INDIVIDU
        fig_ind = go.Figure(go.Indicator(
            mode = "gauge+number", value = cpd_score,
            title = {'text': "Your Clinical Score (%)", 'font': {'size': 16, 'color': '#150a35'}},
            number = {'font': {'color': '#150a35'}},
            gauge = {
                'axis': {'range': [0, 100], 'tickfont': {'color': '#150a35'}}, 
                'bar': {'color': "#150a35", 'thickness': 0.15},
                'steps': [{'range': [0, 65], 'color': "#fecdd3"}, {'range': [65, 75], 'color': "#fb7185"}, {'range': [75, 85], 'color': "#a577ff"}, {'range': [85, 100], 'color': "#6d3ec4"}]
            }
        ))
        fig_ind.update_layout(height=260, margin=dict(t=40, b=10, l=30, r=30), paper_bgcolor='rgba(0,0,0,0)')
        gauge_score_html = fig_ind.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False})

        # GAUGE PEER
        fig_peer = go.Figure(go.Indicator(
            mode = "gauge+number", value = peer_score,
            title = {'text': "Peer Average (%)", 'font': {'size': 16, 'color': '#150a35'}},
            number = {'font': {'color': '#150a35'}},
            gauge = {
                'axis': {'range': [0, 100], 'tickfont': {'color': '#150a35'}}, 
                'bar': {'color': "#6d3ec4", 'thickness': 0.15},
                'steps': [{'range': [0, 65], 'color': "rgba(254, 205, 211, 0.5)"}, {'range': [65, 75], 'color': "rgba(251, 113, 133, 0.5)"}, {'range': [75, 85], 'color': "rgba(244, 63, 94, 0.5)"}, {'range': [85, 100], 'color': "rgba(190, 18, 60, 0.5)"}]
            }
        ))
        fig_peer.update_layout(height=260, margin=dict(t=40, b=10, l=30, r=30), paper_bgcolor='rgba(0,0,0,0)')
        gauge_peer_html = fig_peer.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False})

        if not target_user:
            # Sandaran (Fallback) bagi Doktor Simulasi supaya tolok tidak menjadi 0%
            random.seed(pilihan_doktor)
            cpd_score = random.randint(72, 92)
            history_df = pd.DataFrame([
                {"Date": "14/03/2026", "Category": "CME Workshop", "Topic": "Hypertension Clinical Guidelines", "Marks": "15 pts (APPROVED)"},
                {"Date": "22/02/2026", "Category": "Online Quiz", "Topic": "Medical Ethics & Law", "Marks": "10 pts (APPROVED)"},
                {"Date": "05/01/2026", "Category": "Seminar", "Topic": "Primary Care Dermatology", "Marks": "10 pts (APPROVED)"},
            ])

        if history_df.empty:
            history_html = "<div style='color:#a577ff; font-style:italic;'>No CPD claims found in database.</div>"
        else:
            history_html = history_df.to_html(index=False, classes="custom-table", escape=False)

    # ==========================================
    # TAB 3: DOCUMENTATION (BAR & TABLE)
    # ==========================================
    elif active_tab == 'Documentation':
        # Tarik data dari DB untuk Staf Spesifik
        doc_username_clean = pilihan_doktor.replace("🟢", "").replace("Dr.", "").split(" (")[0].strip()
        target_user = User.objects.filter(username__icontains=doc_username_clean).first()
        if not target_user:
            target_user = User.objects.filter(first_name__icontains=doc_username_clean).first()

        # Isytiharkan nilai awal secara mutlak untuk elak UnboundLocalError
        total_logs = 0
        ontime_pct, delayed_pct, overdue_pct = 0, 0, 0
        delayed_html = "<div style='color:#a577ff; font-style:italic;'>No delayed documentation recorded.</div>"

        if target_user:
            doc_logs = DocumentationLog.objects.filter(officer=target_user)
            total_logs = doc_logs.count()
            if total_logs > 0:
                ontime_count = doc_logs.filter(is_ontime=True).count()
                delayed_count = doc_logs.filter(is_ontime=False, delay_hours__lte=24).count()
                overdue_count = doc_logs.filter(is_ontime=False, delay_hours__gt=24).count()
                
                ontime_pct = int((ontime_count / total_logs) * 100)
                delayed_pct = int((delayed_count / total_logs) * 100)
                overdue_pct = int((overdue_count / total_logs) * 100)
                
                # Jadual Kelewatan Sebenar dari DB
                delays = doc_logs.filter(is_ontime=False).order_by('-delay_hours')[:5]
                if delays.exists():
                    df_delayed = pd.DataFrame([{
                        "Patient ID": d.patient_id,
                        "Delay": f"{d.delay_hours} Hours",
                        "Reason": d.reason_for_delay or "No reason provided",
                        "Date": d.created_at.strftime("%d/%m/%Y")
                    } for d in delays])
                    delayed_html = df_delayed.to_html(index=False, classes="custom-table", escape=False)
        else:
            # Sandaran (Fallback) bagi Doktor Simulasi / Mock Doctor
            total_logs = 20
            ontime_pct, delayed_pct, overdue_pct = 85, 10, 5
            df_delayed = pd.DataFrame({
                "Patient ID": ["RN-88210", "RN-88214"],
                "Delay": ["5.5 Hours", "4.0 Hours"],
                "Reason": ["Ward emergency referral", "System offline backlog"],
                "Date": [date.today().strftime("%d/%m/%Y"), (date.today() - timedelta(days=2)).strftime("%d/%m/%Y")]
            })
            delayed_html = df_delayed.to_html(index=False, classes="custom-table", escape=False)

        # Carta Horizontal Bar
        fig_time = go.Figure()
        if ontime_pct > 0:
            fig_time.add_trace(go.Bar(y=["Patient Records"], x=[ontime_pct], name="On-time", orientation='h', marker=dict(color="#150a35"), text=f"{ontime_pct}%", textposition="inside", insidetextanchor="middle"))
        if delayed_pct > 0:
            fig_time.add_trace(go.Bar(y=["Patient Records"], x=[delayed_pct], name="Delayed (<24h)", orientation='h', marker=dict(color="#a577ff"), text=f"{delayed_pct}%", textposition="inside", insidetextanchor="middle"))
        if overdue_pct > 0:
            fig_time.add_trace(go.Bar(y=["Patient Records"], x=[overdue_pct], name="Overdue (>24h)", orientation='h', marker=dict(color="#fecdd3"), text=f"{overdue_pct}%", textposition="inside", insidetextanchor="middle"))
        
        # Papar jika staf sebenar langsung belum mempunyai sebarang entri log
        if total_logs == 0:
            fig_time.add_trace(go.Bar(y=["Patient Records"], x=[100], name="No Data", orientation='h', marker=dict(color="#e2e8f0"), text="0 Records in DB", textposition="inside"))

        fig_time.update_layout(
            barmode='stack', height=150, margin=dict(l=0, r=0, t=10, b=0),
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            xaxis=dict(range=[0, 100], showgrid=False), yaxis=dict(showgrid=False),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        time_chart_html = fig_time.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False})

    context = {
        'sub_module': sub_module,
        'active_tab': active_tab,
        'senarai_negeri': senarai_negeri,
        'pilihan_negeri': pilihan_negeri,
        'senarai_klinik': senarai_klinik,
        'pilihan_klinik': pilihan_klinik,
        'is_facility_locked': is_facility_locked,
        'is_state_locked': is_state_locked,
        'hod_scope': hod_scope,
        'senarai_doktor': senarai_doktor,
        'pilihan_doktor': pilihan_doktor,
        'chart_html': chart_html,
        'gauge_score_html': gauge_score_html,
        'gauge_peer_html': gauge_peer_html,
        'history_html': history_html,
        'time_chart_html': time_chart_html,
        'delayed_html': delayed_html,
        # TAMBAH 4 BARIS INI
        'cpd_text': cpd_text,
        'cpd_css_class': cpd_css_class, # Tukar dari cpd_color
        'peer_text': peer_text,
        'peer_css_class': peer_css_class, # Tukar dari peer_color
        'active_facilities': active_facilities,
        'total_staff': total_staff,
        'avg_performance': avg_performance,
        'evaluations_completed': evaluations_completed,
        'dept': dept,

        # ... pastikan ia diletakkan di dalam block context = { ... }
        'fms_total_patients': locals().get('fms_total_patients', 0),
        'fms_kadar_kawalan': locals().get('fms_kadar_kawalan', 0),
        'fms_p_value': locals().get('fms_p_value', ''),
        'fms_prestasi': locals().get('fms_prestasi', ''),
        'chart_km_html': locals().get('chart_km_html', ''),
        'chart_gee_html': locals().get('chart_gee_html', ''),
        'box_bg': locals().get('box_bg', '#f8f9fa'),       # <--- TAMBAH INI
        'box_border': locals().get('box_border', '#e2e8f0'), # <--- TAMBAH INI
        'box_text': locals().get('box_text', '#2b3674'),     # <--- TAMBAH INI
        'pilihan_quarter': locals().get('pilihan_quarter', 'Q8'),
        
    }
    return render(request, 'dashboard/clinical_view.html', context)
def _build_hod_staff_picker(dept, state, facility):
    """
    Bina pilihan staf HOD daripada akaun User sebenar.

    Label diselaraskan dengan cts_view supaya pilihan staf kekal apabila
    HOD bertukar antara modul Clinical, Operational dan Interpersonal.
    """
    staff_qs = User.objects.filter(
        is_active=True,
        profile__role='STAFF',
    ).select_related('profile')

    if dept:
        staff_qs = staff_qs.filter(profile__department=dept)

    # Facility yang spesifik lebih tepat daripada state; elakkan tapisan
    # berganda yang boleh menyembunyikan staf yang sah.
    if facility and facility != 'All Clinics':
        staff_qs = staff_qs.filter(profile__assigned_facility=facility)
    elif state and state != 'All States':
        staff_qs = staff_qs.filter(profile__assigned_state=state)

    staff_qs = staff_qs.order_by('first_name', 'last_name', 'username')

    senarai_doktor = []
    staff_by_label = {}
    for staff in staff_qs:
        full_name = f"{staff.first_name} {staff.last_name}".strip()
        display_name = full_name or staff.username

        label = f"🟢 Dr. {display_name} (Live)"
        if label in staff_by_label:
            continue

        senarai_doktor.append(label)
        staff_by_label[label] = staff

    return senarai_doktor, staff_by_label


# ==========================================
# MODUL OPERASIONAL
# ==========================================
@login_required(login_url='/login/')
def operational_view(request):
    sub_module = request.GET.get('sub_module', 'Operational')
    active_tab = request.GET.get('tab', 'Productivity')
    
    # Utamakan Jabatan dari Profil Pengguna berbanding parameter URL
    if hasattr(request.user, 'profile') and request.user.profile.department:
        dept = request.user.profile.department
    else:
        dept = request.GET.get('dept', 'FMS')
    
    # === LOGIK SKOP HOD ===
    hod_scope = getattr(request.user.profile, 'hod_scope', 'NATIONAL')
    hod_state = getattr(request.user.profile, 'assigned_state', None)
    hod_facility = getattr(request.user.profile, 'assigned_facility', None)

    # --- 1. TAPIS NEGERI ---
    senarai_negeri = list(STATE_CENTERS.keys())
    if hod_scope in ['FACILITY', 'STATE'] and hod_state:
        pilihan_negeri = hod_state
    else:
        pilihan_negeri = request.GET.get('state', 'All States')

    # --- 2. TAPIS KLINIK ---
    if pilihan_negeri == "All States":
        klinik_tersedia = MOCK_KLINIK
    else:
        klinik_tersedia = [k for k in MOCK_KLINIK if k["state"] == pilihan_negeri]

    nama_klinik_list = [k["name"] for k in klinik_tersedia]
    senarai_klinik = ["All Clinics"] + nama_klinik_list

    if hod_scope == 'FACILITY' and hod_facility:
        pilihan_klinik = hod_facility
        klinik_tersedia = [k for k in klinik_tersedia if k["name"] == pilihan_klinik]
    else:
        pilihan_klinik = request.GET.get('clinic', 'All Clinics')
        if pilihan_klinik not in senarai_klinik:
            pilihan_klinik = 'All Clinics'
            
    is_facility_locked = (hod_scope == 'FACILITY')
    is_state_locked = (hod_scope == 'STATE')
    
    hod_name = f"{request.user.first_name} {request.user.last_name}".strip()
    if not hod_name:
        hod_name = request.user.username
    hod_facility_label = hod_facility if hod_scope == 'FACILITY' and hod_facility else "HPU Putrajaya"

    # --- 3. TAPIS DOKTOR & KIRA STATISTIK (USER SEBENAR) ---
    senarai_doktor, staff_by_label = _build_hod_staff_picker(
        dept, pilihan_negeri, pilihan_klinik
    )
    semua_markah = [
        calculate_pri_score(staf).get('pri_score', 85)
        for staf in staff_by_label.values()
    ]

    pilihan_doktor = request.GET.get('officer')
    if not pilihan_doktor or pilihan_doktor not in senarai_doktor:
        pilihan_doktor = senarai_doktor[0] if senarai_doktor else "Tiada Pegawai"
    selected_staff = staff_by_label.get(pilihan_doktor)

    # --- PENGIRAAN STATISTIK DINAMIK ---
    active_facilities = len(klinik_tersedia) if pilihan_klinik == "All Clinics" else 1
    total_staff = len(senarai_doktor)
    avg_performance = int(sum(semua_markah) / len(semua_markah)) if semua_markah else 0
    
    random.seed(total_staff)
    evaluations_completed = total_staff * random.randint(3, 6)

    # --- Pembolehubah Carta ---
    area_chart_html = ""
    radar_chart_html = ""

    # 1. Tab Productivity (Area Chart)
    if active_tab == 'Productivity':
        
        # Gunakan staf sebenar yang telah diselesaikan oleh picker.
        target_user = selected_staff

        # Tarik data dari ProductivityLog di Database
        if target_user:
            prod_logs = ProductivityLog.objects.filter(officer=target_user).order_by('record_date')[:14] # Max 14 hari
        else:
            prod_logs = []

        if prod_logs:
            # Jika doktor sudah isi borang, papar rekod sebenar!
            tarikh_label = [log.record_date.strftime("%d %b") for log in prod_logs]
            pesakit_harian = [log.patients_cleared for log in prod_logs]
        else:
            # Fallback jika doktor belum isi apa-apa borang
            tarikh_label = pd.date_range(start="2026-10-01", periods=7).strftime("%d %b").tolist()
            pesakit_harian = [0, 0, 0, 0, 0, 0, 0]
        
        fig_area = go.Figure()
        fig_area.add_trace(go.Scatter(
            x=tarikh_label, 
            y=pesakit_harian, 
            mode='lines+markers',
            fill='tozeroy', 
            line=dict(color='#6d3ec4', width=3, shape='spline', smoothing=1.0),
            marker=dict(size=8, color='#150a35', line=dict(color='white', width=2)),
            fillcolor='rgba(190, 18, 60, 0.15)',
            name='Patients Cleared'
        ))
        
        fig_area.update_layout(
            height=280, 
            margin=dict(l=0, r=0, t=10, b=0), 
            paper_bgcolor='rgba(0,0,0,0)', 
            plot_bgcolor='rgba(0,0,0,0)',
            font=dict(color="#150a35"),
            yaxis=dict(range=[0, 80], gridcolor='#e7dff2'), 
            xaxis_title="Date",               
            yaxis_title="Patients Cleared",   
            xaxis=dict(showgrid=False)
        )
        area_chart_html = fig_area.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False})
    # 2. Tab Innovation (PILIHAN 3: LOLLIPOP CHART)
    elif active_tab == 'Innovation':
        # Gunakan staf sebenar yang telah diselesaikan oleh picker.
        target_user = selected_staff

        # Tetapan Asal
        kategori = ['System Compliance', 'Proactive Thinking', 'Team Delegation', 'Crisis Response']
        markah = [0, 0, 0, 0] # Asal 0

        # Kira purata projek Inovasi DB
        if target_user:
            projects = InnovationProject.objects.filter(officer=target_user)
            if projects.exists():
                avg_score = int(sum(p.score for p in projects) / projects.count())
                # Asumsi ujian: sebarkan skor ini kepada 4 dimensi untuk ilustrasi graf
                markah = [
                    min(100, avg_score + random.randint(-5, 5)),
                    min(100, avg_score + random.randint(-10, 10)),
                    min(100, avg_score + random.randint(-8, 8)),
                    min(100, avg_score + random.randint(-12, 12))
                ]

        fig_pop = go.Figure()

        # 1. Lukiskan tiang lolipop (garisan lurus)
        for i in range(len(kategori)):
            fig_pop.add_shape(
                type='line',
                x0=0, y0=kategori[i], x1=markah[i], y1=kategori[i],
                line=dict(color='#fecdd3', width=4)
            )

        # 2. Lukiskan kepala lolipop (titik dan markah)
        fig_pop.add_trace(go.Scatter(
            x=markah, y=kategori,
            mode='markers+text',
            marker=dict(color='#6d3ec4', size=16, line=dict(color='white', width=2)),
            text=[f"{m}%" if m > 0 else "N/A" for m in markah],
            textposition='middle right',
            textfont=dict(color='#150a35', size=13, weight='bold'),
            hoverinfo='none'
        ))

        fig_pop.update_layout(
            height=320, margin=dict(t=30, b=30, l=10, r=40),
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            font=dict(color="#150a35"), showlegend=False,
            xaxis=dict(range=[0, 105], showgrid=True, gridcolor='#e7dff2', title=dict(text='Score (%)', font=dict(color='#150a35'))),
            yaxis=dict(showgrid=False, tickfont=dict(size=12, color='#150a35', weight='bold'))
        )
        radar_chart_html = fig_pop.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False})
    context = {
        'sub_module': sub_module,
        'active_tab': active_tab,
        'senarai_negeri': senarai_negeri,
        'pilihan_negeri': pilihan_negeri,
        'senarai_klinik': senarai_klinik,       
        'pilihan_klinik': pilihan_klinik,       
        'senarai_doktor': senarai_doktor,
        'pilihan_doktor': pilihan_doktor,
        'area_chart_html': area_chart_html,
        'radar_chart_html': radar_chart_html,
        # --- DATA STATISTIK BAR SISI ---
        'active_facilities': active_facilities,
        'total_staff': total_staff,
        'avg_performance': avg_performance,
        'evaluations_completed': evaluations_completed,
        'dept': dept,
        'is_facility_locked': is_facility_locked,
        'is_state_locked': is_state_locked,
        'hod_scope': hod_scope,
        'hod_name': hod_name,
        'hod_facility_label': hod_facility_label,
    }
    return render(request, 'dashboard/operational_view.html', context)

# ==========================================
# MODUL INTERPERSONAL
# ==========================================
@login_required(login_url='/login/')
def interpersonal_view(request):
    sub_module = request.GET.get('sub_module', 'Interpersonal')
    active_tab = request.GET.get('tab', 'Patient')
    
    # Utamakan Jabatan dari Profil Pengguna berbanding parameter URL
    if hasattr(request.user, 'profile') and request.user.profile.department:
        dept = request.user.profile.department
    else:
        dept = request.GET.get('dept', 'FMS')
    
    # === LOGIK SKOP HOD ===
    hod_scope = getattr(request.user.profile, 'hod_scope', 'NATIONAL')
    hod_state = getattr(request.user.profile, 'assigned_state', None)
    hod_facility = getattr(request.user.profile, 'assigned_facility', None)

    # --- 1. TAPIS NEGERI ---
    senarai_negeri = list(STATE_CENTERS.keys())
    if hod_scope in ['FACILITY', 'STATE'] and hod_state:
        pilihan_negeri = hod_state
    else:
        pilihan_negeri = request.GET.get('state', 'All States')

    # --- 2. TAPIS KLINIK ---
    if pilihan_negeri == "All States":
        klinik_tersedia = MOCK_KLINIK
    else:
        klinik_tersedia = [k for k in MOCK_KLINIK if k["state"] == pilihan_negeri]

    nama_klinik_list = [k["name"] for k in klinik_tersedia]
    senarai_klinik = ["All Clinics"] + nama_klinik_list

    if hod_scope == 'FACILITY' and hod_facility:
        pilihan_klinik = hod_facility
        klinik_tersedia = [k for k in klinik_tersedia if k["name"] == pilihan_klinik]
    else:
        pilihan_klinik = request.GET.get('clinic', 'All Clinics')
        if pilihan_klinik not in senarai_klinik:
            pilihan_klinik = 'All Clinics'
            
    is_facility_locked = (hod_scope == 'FACILITY')
    is_state_locked = (hod_scope == 'STATE')
    
    hod_name = f"{request.user.first_name} {request.user.last_name}".strip()
    if not hod_name:
        hod_name = request.user.username
    hod_facility_label = hod_facility if hod_scope == 'FACILITY' and hod_facility else "HPU Putrajaya"

    # --- 3. TAPIS DOKTOR & KIRA STATISTIK (USER SEBENAR) ---
    senarai_doktor, staff_by_label = _build_hod_staff_picker(
        dept, pilihan_negeri, pilihan_klinik
    )
    semua_markah = [
        calculate_pri_score(staf).get('pri_score', 85)
        for staf in staff_by_label.values()
    ]

    pilihan_doktor = request.GET.get('officer')
    if not pilihan_doktor or pilihan_doktor not in senarai_doktor:
        pilihan_doktor = senarai_doktor[0] if senarai_doktor else "Tiada Pegawai"
    selected_staff = staff_by_label.get(pilihan_doktor)

    # ==========================================
    # LOGIK INTEGRASI MyPSQ HOD (FASA 4)
    # ==========================================
    # Gunakan staf sebenar yang telah diselesaikan oleh picker.
    target_user = selected_staff

    if target_user:
        mypsq_feedbacks = MyPSQFeedback.objects.filter(doctor=target_user).order_by('-created_at')
    else:
        mypsq_feedbacks = MyPSQFeedback.objects.none()

    mypsq_count = mypsq_feedbacks.count()

    if mypsq_count > 0:
        avg_q = sum([f.average_score for f in mypsq_feedbacks]) / mypsq_count
        mypsq_avg_score = round(avg_q, 1)
        mypsq_pct = int((avg_q / 5.0) * 100)
        
        # Purata bagi 7 Dimensi Teras MyPSQ
        dim_scores = {
            'q1': round(sum([f.q1_general for f in mypsq_feedbacks]) / mypsq_count, 1),
            'q2': round(sum([f.q2_technical for f in mypsq_feedbacks]) / mypsq_count, 1),
            'q3': round(sum([f.q3_interpersonal for f in mypsq_feedbacks]) / mypsq_count, 1),
            'q4': round(sum([f.q4_communication for f in mypsq_feedbacks]) / mypsq_count, 1),
            'q5': round(sum([f.q5_financial for f in mypsq_feedbacks]) / mypsq_count, 1),
            'q6': round(sum([f.q6_time for f in mypsq_feedbacks]) / mypsq_count, 1),
            'q7': round(sum([f.q7_accessibility for f in mypsq_feedbacks]) / mypsq_count, 1),
        }
    elif target_user:
        # Fallback rawak terkawal berasaskan seed nama doktor jika DB belum ada entri
        random.seed(pilihan_doktor + "mypsq_hod")
        mypsq_avg_score = round(random.uniform(4.2, 4.9), 1)
        mypsq_pct = int((mypsq_avg_score / 5.0) * 100)
        mypsq_count = random.randint(12, 38)
        dim_scores = {
            'q1': round(random.uniform(4.0, 5.0), 1),
            'q2': round(random.uniform(4.2, 5.0), 1),
            'q3': round(random.uniform(4.5, 5.0), 1),
            'q4': round(random.uniform(4.1, 4.9), 1),
            'q5': round(random.uniform(4.0, 4.8), 1),
            'q6': round(random.uniform(3.8, 4.7), 1),
            'q7': round(random.uniform(4.0, 4.8), 1),
        }
    else:
        # Tiada staf dipilih: jangan reka data MyPSQ.
        mypsq_avg_score = 0.0
        mypsq_pct = 0
        mypsq_count = 0
        dim_scores = {'q1': 0.0, 'q2': 0.0, 'q3': 0.0, 'q4': 0.0, 'q5': 0.0, 'q6': 0.0, 'q7': 0.0}

    # --- PENGIRAAN STATISTIK DINAMIK ---
    active_facilities = len(klinik_tersedia) if pilihan_klinik == "All Clinics" else 1
    total_staff = len(senarai_doktor)
    avg_performance = int(sum(semua_markah) / len(semua_markah)) if semua_markah else 0
    
    # Guna import global tanpa mendefinisikan semula 'import random' tempatan
    random.seed(total_staff)
    evaluations_completed = total_staff * random.randint(3, 6)

    # --- PEMBOLEHUBAH HTML ---
    radar_chart_html = ""
    ei_chart_html = ""
    team_360_html = ""
    team_scatter_html = ""
    
    pts_gen, pts_proc, pts_cons = 0, 0, 0 # Pembolehubah untuk point breakdown

    # 1. VISUALISASI UTAMA (PILIHAN 3: POLAR AREA / ROSE CHART)
    kategori_radar = ['Patient Experience', 'Emotional Intelligence', 'Teamwork', 'Mentorship', 'Communication']
    
    if selected_staff:
        random.seed(pilihan_doktor + "softskills")
        markah_radar = [random.randint(65, 95) for _ in range(5)]
    else:
        markah_radar = [0, 0, 0, 0, 0]
    
    # 5 tona warna pelbagai untuk nampak menarik (TEMA ROSE KORPORAT)
    warna_rose = ['#150a35', '#6d3ec4', '#e11d48', '#fb7185', '#fecdd3']

    fig_rose = go.Figure()
    fig_rose.add_trace(go.Barpolar(
        r=markah_radar,
        theta=kategori_radar,
        marker_color=warna_rose,
        marker_line_color='white',
        marker_line_width=2,
        opacity=0.85
    ))

    fig_rose.update_layout(
        polar=dict(
            radialaxis=dict(visible=True, range=[0, 100], showgrid=True, gridcolor='#e2e8f0'),
            angularaxis=dict(showgrid=True, gridcolor='#e2e8f0', linecolor='#e2e8f0', tickfont=dict(size=11, color='#2b3674', weight='bold'), direction='clockwise')
        ),
        showlegend=False, height=320, margin=dict(t=30, b=30, l=30, r=30), paper_bgcolor='rgba(0,0,0,0)'
    )
    radar_chart_html = fig_rose.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False})
    # 2. LOGIK TAB KANDUNGAN
    # Gunakan staf sebenar yang telah diselesaikan oleh picker.
    target_user = selected_staff

    if active_tab == 'EI':
        ei_comp = [label for _post_key, _field_name, label in EI_DOMAIN_FIELDS]
        ei_domain_scores = [None] * len(EI_DOMAIN_FIELDS)

        if target_user:
            reflections = list(
                EmotionalReflection.objects.filter(officer=target_user)
            )
            if reflections:
                ei_domain_scores = _average_reflection_domains(reflections)

        ei_scores = [
            score if score is not None else 0
            for score in ei_domain_scores
        ]

        warna_palang = ['#fecdd3', '#fda4af', '#e11d48', '#150a35']
        
        fig_ei = go.Figure(go.Bar(
            x=ei_scores, y=ei_comp, orientation='h', 
            marker_color=warna_palang,
            text=[
                f"{score}%" if score is not None else "No Data"
                for score in ei_domain_scores
            ],
            textposition='auto',
            textfont=dict(color='white', weight='bold')
        ))
        fig_ei.update_layout(
            height=220, margin=dict(t=10, b=10, l=0, r=0), 
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            xaxis=dict(range=[0, 100], showgrid=False)
        )
        ei_chart_html = fig_ei.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False})

    elif active_tab == 'Teamwork':
        teamwork_view = request.GET.get('t_view', '360')
        
        if teamwork_view == '360':
            roles = ["Peers", "Support Staff", "Supervisors"]
            fb_scores = [0, 0, 0]

            if target_user:
                # Kira Kudos Points (1 Kudos = 5 mata tambahan dari base score 70)
                kudos_received = PeerKudos.objects.filter(receiver=target_user)
                if kudos_received.exists():
                    total_kudos = sum(k.points for k in kudos_received)
                    base_kudos = min(100, 70 + total_kudos)
                    fb_scores = [base_kudos, min(100, base_kudos + random.randint(-5, 10)), min(100, base_kudos + random.randint(-8, 12))]
            
            fig_team = go.Figure(go.Bar(
                x=roles, y=fb_scores, marker_color='#6d3ec4', 
                text=[f"{s}%" if s > 0 else "0%" for s in fb_scores], textposition='auto'
            ))
            fig_team.update_layout(
                height=220, margin=dict(t=20, b=20, l=0, r=0), 
                paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                yaxis=dict(range=[0, 100])
            )
            team_360_html = fig_team.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False})
            
        elif teamwork_view == 'Log':
            categories = ["General Clinical", "Procedural", "Consultation"]
            color_map = {"General Clinical": "#150a35", "Procedural": "#e11d48", "Consultation": "#fb7185"}
            fig_scatter = go.Figure()

            if target_user:
                kudos_list = PeerKudos.objects.filter(receiver=target_user).order_by('sent_at')
                if kudos_list.exists():
                    df_contrib = pd.DataFrame([{
                        "Date": k.sent_at.strftime("%Y-%m-%d"),
                        "Category": k.category,
                        "Points": k.points
                    } for k in kudos_list])

                    pts_gen = df_contrib[df_contrib['Category'] == 'General Clinical']['Points'].sum() * 5
                    pts_proc = df_contrib[df_contrib['Category'] == 'Procedural']['Points'].sum() * 5
                    pts_cons = df_contrib[df_contrib['Category'] == 'Consultation']['Points'].sum() * 5
                    
                    for cat in categories:
                        if cat in df_contrib['Category'].values:
                            cat_df = df_contrib[df_contrib['Category'] == cat]
                            fig_scatter.add_trace(go.Scatter(
                                x=cat_df['Date'], y=cat_df['Points'], mode='markers', name=cat,
                                marker=dict(size=12, color=color_map[cat], line=dict(width=1, color='white'))
                            ))
            else:
                pts_gen, pts_proc, pts_cons = 0, 0, 0
                
            fig_scatter.update_layout(
                height=260, margin=dict(l=0, r=0, t=30, b=0),
                paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                yaxis_title="Points per Event", yaxis=dict(range=[0, 10], showgrid=True, gridcolor='#e2e8f0'),
                xaxis=dict(showgrid=False),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5, font=dict(size=11))
            )
            team_scatter_html = fig_scatter.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False})

    elif active_tab == 'Teamwork':
        teamwork_view = request.GET.get('t_view', '360')
        
        if teamwork_view == '360':
            np.random.seed(len(pilihan_doktor) + 20)
            roles = ["Peers", "Support Staff", "Supervisors"]
            fb_scores = [np.random.randint(80, 98) for _ in range(3)]
            
            fig_team = go.Figure(go.Bar(
                x=roles, y=fb_scores, marker_color='#6d3ec4', text=fb_scores, textposition='auto'
            ))
            fig_team.update_layout(
                height=220, margin=dict(t=20, b=20, l=0, r=0), 
                paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                yaxis=dict(range=[0, 100])
            )
            team_360_html = fig_team.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False})
            
        elif teamwork_view == 'Log':
            t_filter = request.GET.get('t_filter', 'Month')
            np.random.seed(len(pilihan_doktor) + len(t_filter))
            
            if t_filter == 'Week':
                dates = pd.date_range(start="2026-02-20", end="2026-02-26", freq='D')
                num_dots = np.random.randint(3, 7) # Kurangkan had maksimum untuk mingguan
            elif t_filter == 'Month':
                dates = pd.date_range(start="2026-02-01", end="2026-02-28", freq='D')
                num_dots = np.random.randint(15, 25)
            else: 
                dates = pd.date_range(start="2025-03-01", end="2026-02-28", freq='5D')
                num_dots = np.random.randint(30, 50)
                
            # PENYELAMAT: Pastikan titik tidak melebihi saiz tarikh yang ada
            num_dots = min(num_dots, len(dates))
            selected_dates = np.random.choice(dates, num_dots, replace=False)
            categories = ["General Clinical", "Procedural", "Consultation"]
            
            # Guna markah 1 hingga 9 sahaja supaya titik lebih kemas seperti asal
            df_contrib = pd.DataFrame({
                "Date": selected_dates,
                "Category": np.random.choice(categories, num_dots),
                "Points": np.random.randint(1, 10, num_dots) 
            }).sort_values("Date")
            
            # Darab dengan 5 untuk panel tepi supaya nampak markah keseluruhan yang logik
            pts_gen = df_contrib[df_contrib['Category'] == 'General Clinical']['Points'].sum() * 5
            pts_proc = df_contrib[df_contrib['Category'] == 'Procedural']['Points'].sum() * 5
            pts_cons = df_contrib[df_contrib['Category'] == 'Consultation']['Points'].sum() * 5
            
            # Scatter Plot 
            fig_scatter = go.Figure()
            color_map = {"General Clinical": "#150a35", "Procedural": "#e11d48", "Consultation": "#fb7185"}
            
            for cat in categories:
                cat_df = df_contrib[df_contrib['Category'] == cat]
                fig_scatter.add_trace(go.Scatter(
                    x=cat_df['Date'], y=cat_df['Points'], mode='markers', name=cat,
                    # KUNCI UTAMA: Saiz titik dikunci kepada saiz 8 (Bukan mengembang ikut markah)
                    marker=dict(size=8, color=color_map[cat], line=dict(width=1, color='white'))
                ))
            
            fig_scatter.update_layout(
                height=260, margin=dict(l=0, r=0, t=30, b=0),
                paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                yaxis_title="Points per Event",
                # Paksa y-axis dari 0 hingga 10 supaya jaraknya teratur
                yaxis=dict(range=[0, 10], showgrid=True, gridcolor='#e2e8f0'),
                xaxis=dict(showgrid=False),
                # Letak legend (petunjuk) di tengah-tengah atas seperti gambar lama
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5, font=dict(size=11))
            )
            team_scatter_html = fig_scatter.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False})

    context = {
        'sub_module': sub_module,
        'active_tab': active_tab,
        'senarai_negeri': senarai_negeri,
        'pilihan_negeri': pilihan_negeri,
        'senarai_klinik': senarai_klinik,      
        'pilihan_klinik': pilihan_klinik,       
        'senarai_doktor': senarai_doktor,
        'pilihan_doktor': pilihan_doktor,
        'radar_chart_html': radar_chart_html,
        'ei_chart_html': ei_chart_html,
        'team_360_html': team_360_html,
        'team_scatter_html': team_scatter_html,
        't_view': request.GET.get('t_view', '360'),
        't_filter': request.GET.get('t_filter', 'Month'),
        'pts_gen': pts_gen, 'pts_proc': pts_proc, 'pts_cons': pts_cons,
        'active_facilities': active_facilities,
        'total_staff': total_staff,
        'avg_performance': avg_performance,
        'evaluations_completed': evaluations_completed,
        'dept': dept,
        'is_facility_locked': is_facility_locked,
        'is_state_locked': is_state_locked,
        'hod_scope': hod_scope,
        'hod_name': hod_name,
        'hod_facility_label': hod_facility_label,
        
        # --- DATA MyPSQ UNTUK PORTAL HOD ---
        'mypsq_avg_score': mypsq_avg_score,
        'mypsq_pct': mypsq_pct,
        'mypsq_count': mypsq_count,
        'mypsq_feedbacks': mypsq_feedbacks,
        'dim_scores': dim_scores,
    }
    return render(request, 'dashboard/interpersonal_view.html', context)

# ==========================================
# MODUL STAFF (Staff PORTAL)
# ==========================================
def _parse_int(value, default=0):
    """Tukar nilai POST kepada int tanpa menyebabkan ValueError."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _parse_float(value, default=0.0):
    """Tukar nilai POST kepada float tanpa menyebabkan ValueError."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_date_value(value, default=None):
    """Terima format YYYY-MM-DD daripada <input type='date'>."""
    if not value:
        return default
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return default


def _parse_datetime_value(value, default=None):
    """Terima format datetime-local dan pulangkan datetime timezone-aware."""
    if not value:
        return default
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return default

    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(
            parsed,
            timezone.get_default_timezone(),
        )
    return parsed


def _hash_patient_ref(value):
    """
    Tukar MRN/ID pesakit kepada rujukan pseudonim SHA-256 (16 aksara).
    Nilai asal TIDAK disimpan di pangkalan data.
    """
    raw = (value or '').strip().upper()
    if not raw:
        raw = f"UNKNOWN-{date.today().isoformat()}"
    digest = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16].upper()
    return f"REF-{digest}"


# Nilai checkbox q1-q3 mesti sepadan dengan atribut value dalam _clinical.html.
POLICY_MULTI_OPTIONS = {
    1: (
        'ascvd',
        'visual',
        'neuro',
        'renal',
        'foot',
        'sexual',
    ),
    2: (
        'bmi_waist',
        'eye_retinopathy',
        'foot_exam',
        'neuropathy',
    ),
    3: (
        'family_history',
        'lifestyle',
        'comorbid',
        'obstetric',
    ),
}

POLICY_DOMAIN_QUESTIONS = {
    'a': (1, 2),
    'b': (3, 4, 5, 6, 7),
    'c': (8, 9, 10),
    'd': (11, 12, 13),
    'e': (14, 15, 16, 17, 18, 19, 20),
}


MAX_CPD_CERTIFICATE_SIZE = 5 * 1024 * 1024
ALLOWED_CPD_CERTIFICATE_EXTENSIONS = ['pdf', 'jpg', 'jpeg', 'png']
CPD_CERTIFICATE_SIGNATURES = {
    'pdf': (b'%PDF-',),
    'jpg': (b'\xff\xd8\xff',),
    'jpeg': (b'\xff\xd8\xff',),
    'png': (b'\x89PNG\r\n\x1a\n',),
}


def _validate_cpd_certificate(uploaded_file):
    """
    Sahkan sijil CPD di server sebelum disimpan.

    Semakan merangkumi saiz, sambungan fail, dan magic bytes asas supaya
    fail yang hanya ditukar nama tidak diterima.
    """
    if not uploaded_file:
        return

    if uploaded_file.size == 0:
        raise ValidationError('Fail sijil kosong.')

    if uploaded_file.size > MAX_CPD_CERTIFICATE_SIZE:
        raise ValidationError('Saiz fail sijil melebihi 5MB.')

    try:
        FileExtensionValidator(ALLOWED_CPD_CERTIFICATE_EXTENSIONS)(uploaded_file)
    except ValidationError:
        raise ValidationError(
            'Sijil mesti dalam format PDF, JPG, atau PNG.'
        ) from None

    extension = uploaded_file.name.rsplit('.', 1)[-1].lower()
    header = uploaded_file.read(8)
    uploaded_file.seek(0)

    signatures = CPD_CERTIFICATE_SIGNATURES.get(extension, ())
    if not any(header.startswith(signature) for signature in signatures):
        raise ValidationError(
            'Kandungan fail tidak sepadan dengan format sijil yang dibenarkan.'
        )


def _kudos_category_points(value):
    """Pulangkan (kategori, mata) berdasarkan pilihan dropdown Teamwork."""
    mapping = {
        'Shift Cover & Ward Relief': 10,
        'Emergency & On-Call Assistance': 10,
        'Clinical Knowledge Sharing & CME': 5,
        'Empathy & Team Moral Support': 5,
    }
    return (value or 'Peer Endorsement')[:50], mapping.get(value, 5)


MENTORSHIP_DURATION_HOURS = {
    '30 mins': 0.5,
    '1 Hour': 1.0,
    '2 Hours': 2.0,
}


def _mentor_duration_hours(value):
    """Tukar pilihan durasi mentorship yang sah kepada jam."""
    selected = _required_choice(
        value,
        MENTORSHIP_DURATION_HOURS,
        'Session Duration',
    )
    return MENTORSHIP_DURATION_HOURS[selected]


# Nilai di bawah mesti sepadan dengan atribut value dalam _clinical.html.
CTS_FACILITIES = ('Heart Center IJN', 'Hospital Utama')
FMS_FACILITIES = ('Klinik Kesihatan Medong', 'Klinik Kesihatan Bandar')
CTS_PROCEDURES = (
    'LIMA-LAD Procedure',
    'CABG (Coronary Artery Bypass Graft)',
    'Valve Replacement',
    'Aortic Surgery',
    'Congenital Heart Surgery',
)
FMS_CONDITIONS = (
    'Type 2 Diabetes Mellitus (T2DM)',
    'Hypertension',
    'Asthma / COPD',
    'Dyslipidaemia',
)
CTS_PRIMARY_DIAGNOSES = (
    'Coronary Artery Disease (CAD)',
    'Valvular Heart Disease',
    'Aortic Aneurysm / Dissection',
    'Congenital Heart Defect',
)
CTS_PREVIOUS_INTERVENTIONS = (
    'None',
    'Previous PCI / Stenting',
    'Previous CABG',
    'Previous Valve Surgery',
)
CTS_CORONARY_ANATOMY = (
    'Single Vessel Disease',
    'Double Vessel Disease',
    'Triple Vessel Disease (TVD)',
    'Left Main Disease',
)
CTS_NYHA_CLASSES = ('I', 'II', 'III', 'IV')
FMS_DURATION_ILLNESS = (
    'Newly Diagnosed (< 1 Year)',
    '1 - 5 Years',
    '> 5 Years',
)
CTS_COMORBIDITIES = (
    'Diabetes (on Insulin)',
    'Hypertension',
    'Dyslipidaemia',
    'Renal Impairment (Dialysis)',
    'COPD / Asthma',
    'Extracardiac Arteriopathy',
    'Previous Stroke / TIA',
    'Active Smoker',
)
FMS_COMORBIDITIES = (
    'Active Smoker',
    'Obesity (BMI > 27.5)',
    'Ischaemic Heart Disease',
    'Chronic Kidney Disease (CKD)',
    'Diabetic Retinopathy',
)
CLINICAL_COMORBIDITY_NONE = 'None'
BP_PATTERN = re.compile(r'^\s*(\d{1,3})\s*/\s*(\d{1,3})\s*$')

PRODUCTIVITY_PUNCTUALITY_RATES = {
    'On-Time': 100.0,
    'Slight Delay': 85.0,
    'Ward Round Extension': 70.0,
}
INNOVATION_TYPES = (
    'Research',
    'Clinical Trial',
    'Technology Adoption',
    'Publication',
)
INNOVATION_RESEARCH_ROLES = (
    'Principle Investigator (PI)',
    'Co-Investigator',
)
INNOVATION_TRIAL_PHASES = (
    'Phase I',
    'Phase II',
    'Phase III',
    'Phase IV (Post-Market)',
)
INNOVATION_TRIAL_STATUSES = (
    'Active',
    'Closed',
)
INNOVATION_PROFICIENCY_LEVELS = (
    'Amateur (Learning)',
    'Intermediate (Independent)',
    'Expert (Trainer)',
)
EI_DOMAIN_FIELDS = (
    ('ei_self_awareness', 'self_awareness_score', 'Self-Awareness'),
    ('ei_regulation', 'self_regulation_score', 'Self-Regulation'),
    ('ei_empathy', 'empathy_score', 'Empathy'),
    ('ei_deescalation', 'deescalation_score', 'De-escalation'),
)
EI_TRIGGER_EVENTS = (
    'Aggressive Patient/Attendant',
    'Critical Patient Mortality',
    'On-Call Fatigue & Workload',
    'Inter-departmental Miscommunication',
)
EI_COPING_STRATEGIES = (
    'De-escalation Protocol Applied',
    'Peer Debriefing Session',
    'Short Tactical Pause',
    'HOD Consultation',
)
MENTORSHIP_ROLES = tuple(
    value for value, _label in MentorshipSession.ROLE_CHOICES
)
MENTORSHIP_DOMAINS = (
    'Bedside Clinical Supervision',
    'Postgraduate & Parallel Pathway',
    'On-Call Coping & Stress Adaptation',
    'Audit & Research Guidance',
)


def _required_choice(value, allowed, label):
    """Sahkan pilihan daripada whitelist dan pulangkan teks asal."""
    selected = (value or '').strip()
    if not selected:
        raise ValidationError(f'{label} diperlukan.')
    if selected not in allowed:
        raise ValidationError(f'{label} tidak sah.')
    return selected


def _optional_choice(value, allowed, label):
    """Sahkan pilihan optional; kosong menjadi None."""
    selected = (value or '').strip()
    if not selected:
        return None
    if selected not in allowed:
        raise ValidationError(f'{label} tidak sah.')
    return selected


def _required_float(value, label, minimum=None, maximum=None):
    """Tukar input wajib kepada float dan sahkan julatnya."""
    raw = (value or '').strip()
    if not raw:
        raise ValidationError(f'{label} diperlukan.')
    try:
        parsed = float(raw)
    except (TypeError, ValueError):
        raise ValidationError(f'{label} mesti nombor yang sah.') from None

    if not math.isfinite(parsed):
        raise ValidationError(f'{label} mesti nombor yang sah.')
    if minimum is not None and maximum is not None:
        if parsed < minimum or parsed > maximum:
            raise ValidationError(
                f'{label} mesti antara {minimum:g} hingga {maximum:g}.'
            )
    elif minimum is not None and parsed < minimum:
        raise ValidationError(f'{label} mesti sekurang-kurangnya {minimum:g}.')
    elif maximum is not None and parsed > maximum:
        raise ValidationError(f'{label} tidak boleh melebihi {maximum:g}.')
    return parsed


def _optional_float(value, label):
    """Tukar input optional kepada float; kosong menjadi None."""
    raw = (value or '').strip()
    if not raw:
        return None
    try:
        parsed = float(raw)
    except (TypeError, ValueError):
        raise ValidationError(f'{label} mesti nombor yang sah.') from None

    if not math.isfinite(parsed):
        raise ValidationError(f'{label} mesti nombor yang sah.')
    return parsed


def _required_date(value, label):
    """Sahkan tarikh wajib daripada input type=date."""
    parsed = _parse_date_value(value)
    if parsed is None:
        raise ValidationError(f'{label} tidak sah atau kosong.')
    return parsed


def _optional_date(value, label):
    """Sahkan tarikh optional; kosong menjadi None, format salah ditolak."""
    raw = (value or '').strip()
    if not raw:
        return None
    parsed = _parse_date_value(raw)
    if parsed is None:
        raise ValidationError(f'{label} tidak sah.')
    return parsed


def _optional_text(value, max_length=None):
    """Bersihkan teks optional; kosong menjadi None."""
    cleaned = (value or '').strip()
    if not cleaned:
        return None
    return cleaned[:max_length] if max_length else cleaned


def _required_text(value, label, max_length=None):
    """Bersihkan teks wajib dan pastikan ia tidak kosong."""
    cleaned = _optional_text(value, max_length)
    if not cleaned:
        raise ValidationError(f'{label} diperlukan.')
    return cleaned


def _optional_int(value, label, minimum=None, maximum=None):
    """Tukar input optional kepada int dan sahkan julatnya."""
    raw = (value or '').strip()
    if not raw:
        return None
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        raise ValidationError(f'{label} mesti nombor bulat yang sah.') from None

    if minimum is not None and maximum is not None:
        if parsed < minimum or parsed > maximum:
            raise ValidationError(
                f'{label} mesti antara {minimum} hingga {maximum}.'
            )
    elif minimum is not None and parsed < minimum:
        raise ValidationError(f'{label} mesti sekurang-kurangnya {minimum}.')
    elif maximum is not None and parsed > maximum:
        raise ValidationError(f'{label} tidak boleh melebihi {maximum}.')
    return parsed


def _required_int(value, label, minimum=None, maximum=None):
    """Tukar input wajib kepada int dan sahkan julatnya."""
    parsed = _optional_int(value, label, minimum=minimum, maximum=maximum)
    if parsed is None:
        raise ValidationError(f'{label} diperlukan.')
    return parsed


def _parse_bp(value):
    """Pecahkan '135/85' kepada systolic dan diastolic yang sah."""
    raw = (value or '').strip()
    if not raw:
        raise ValidationError('Tekanan darah diperlukan.')

    match = BP_PATTERN.match(raw)
    if not match:
        raise ValidationError(
            'Tekanan darah mesti dalam format SBP/DBP, contoh 135/85.'
        )

    systolic, diastolic = (int(part) for part in match.groups())
    if not (50 <= systolic <= 300 and 30 <= diastolic <= 200):
        raise ValidationError(
            'Tekanan darah di luar julat: systolic 50-300, diastolic 30-200.'
        )
    if systolic <= diastolic:
        raise ValidationError(
            'Systolic BP mesti lebih tinggi daripada diastolic BP.'
        )
    return systolic, diastolic


def _normalise_comorbidities(values, allowed):
    """Kekalkan pilihan whitelist, buang duplicate/None/unknown."""
    selected = {
        str(value).strip()
        for value in (values or [])
        if str(value).strip()
    }
    if not selected or selected == {CLINICAL_COMORBIDITY_NONE}:
        return []
    return [option for option in allowed if option in selected]


def _save_clinical_procedure(request, form_type, profile):
    """Validasi dan simpan satu rekod klinikal CTS/FMS."""
    if form_type == 'skills':
        clinical_domain = (
            'CTS'
            if (getattr(profile, 'department', '') or '').upper() == 'CTS'
            else 'FMS'
        )
    else:
        clinical_domain = 'CTS' if form_type == 'skills_cts' else 'FMS'

    procedure_date = _required_date(
        request.POST.get('procedure_date'),
        'Tarikh prosedur',
    )
    score_val = _required_float(
        request.POST.get('score'),
        'Skor prosedur',
        minimum=0.0,
        maximum=100.0,
    )

    if clinical_domain == 'CTS':
        facility = _required_choice(
            request.POST.get('facility'),
            CTS_FACILITIES,
            'Hospital / Center',
        )
        proc_name = _required_choice(
            request.POST.get('procedure_name'),
            CTS_PROCEDURES,
            'Procedure Performed',
        )
        primary_diagnosis = _required_choice(
            request.POST.get('primary_diagnosis'),
            CTS_PRIMARY_DIAGNOSES,
            'Primary Diagnosis',
        )
        previous_intervention = _optional_choice(
            request.POST.get('previous_intervention'),
            CTS_PREVIOUS_INTERVENTIONS,
            'Previous Cardiac Intervention',
        )
        comorbidities = _normalise_comorbidities(
            request.POST.getlist('comorbidities'),
            CTS_COMORBIDITIES,
        )
        lvef_percent = _required_float(
            request.POST.get('lvef_percent'),
            'LVEF',
            minimum=1.0,
            maximum=100.0,
        )
        coronary_anatomy = _optional_choice(
            request.POST.get('coronary_anatomy'),
            CTS_CORONARY_ANATOMY,
            'Coronary Anatomy',
        )
        nyha_class = _required_choice(
            request.POST.get('nyha_class'),
            CTS_NYHA_CLASSES,
            'NYHA Functional Class',
        )
        duration_illness = None
        systolic_bp = None
        diastolic_bp = None
        hba1c_percent = None
        ldl_mmol_l = None
        target_achieved = None
    else:
        facility = _required_choice(
            request.POST.get('facility'),
            FMS_FACILITIES,
            'Clinic Location',
        )
        proc_name = _required_choice(
            request.POST.get('procedure_name'),
            FMS_CONDITIONS,
            'Primary Chronic Disease Monitored',
        )
        duration_illness = _optional_choice(
            request.POST.get('duration_illness'),
            FMS_DURATION_ILLNESS,
            'Duration of Illness',
        )
        comorbidities = _normalise_comorbidities(
            request.POST.getlist('comorbidities'),
            FMS_COMORBIDITIES,
        )
        systolic_bp, diastolic_bp = _parse_bp(
            request.POST.get('bp')
        )
        hba1c_percent = _optional_float(
            request.POST.get('hba1c_percent'),
            'HbA1c',
        )
        ldl_mmol_l = _optional_float(
            request.POST.get('ldl_mmol_l'),
            'LDL Cholesterol',
        )
        target_raw = (request.POST.get('target') or '').strip()
        if target_raw not in ('Yes', 'No'):
            raise ValidationError('Sila pilih status pencapaian target.')
        target_achieved = (target_raw == 'Yes')
        primary_diagnosis = None
        previous_intervention = None
        lvef_percent = None
        coronary_anatomy = None
        nyha_class = None

    with transaction.atomic():
        ProcedureLog.objects.create(
            officer=request.user,
            patient_ref=None,
            clinical_domain=clinical_domain,
            facility=facility,
            procedure_name=proc_name,
            procedure_date=procedure_date,
            primary_diagnosis=primary_diagnosis,
            previous_intervention=previous_intervention,
            comorbidities=comorbidities,
            lvef_percent=lvef_percent,
            coronary_anatomy=coronary_anatomy,
            nyha_class=nyha_class,
            duration_illness=duration_illness,
            systolic_bp=systolic_bp,
            diastolic_bp=diastolic_bp,
            hba1c_percent=hba1c_percent,
            ldl_mmol_l=ldl_mmol_l,
            is_successful=True,
            complication_occurred=False,
            target_achieved=target_achieved,
            score=score_val,
        )

    return clinical_domain, proc_name, score_val, procedure_date


def _average_reflection_domains(reflections):
    """Purata setiap domain EI; None jika tiada nilai sah untuk domain itu."""
    averages = []
    for _post_key, field_name, _label in EI_DOMAIN_FIELDS:
        values = [
            getattr(reflection, field_name)
            for reflection in reflections
            if getattr(reflection, field_name) is not None
        ]
        averages.append(round(sum(values) / len(values)) if values else None)
    return averages


def _save_emotional_reflection(request):
    """Validasi dan simpan satu rekod EmotionalReflection."""
    domain_scores = {}
    for post_key, field_name, label in EI_DOMAIN_FIELDS:
        domain_scores[field_name] = _required_int(
            request.POST.get(post_key),
            f'{label} Score',
            minimum=0,
            maximum=100,
        )

    trigger_event = _required_choice(
        request.POST.get('ei_trigger'),
        EI_TRIGGER_EVENTS,
        'Stress Trigger Event',
    )
    coping_strategy = _required_choice(
        request.POST.get('ei_coping'),
        EI_COPING_STRATEGIES,
        'Coping Action Strategy',
    )
    reflection_notes = _required_text(
        request.POST.get('ei_reflection'),
        'Qualitative Reflection',
    )

    with transaction.atomic():
        return EmotionalReflection.objects.create(
            officer=request.user,
            self_awareness_score=domain_scores['self_awareness_score'],
            self_regulation_score=domain_scores['self_regulation_score'],
            empathy_score=domain_scores['empathy_score'],
            deescalation_score=domain_scores['deescalation_score'],
            trigger_event=trigger_event,
            coping_strategy=coping_strategy,
            reflection_notes=reflection_notes,
        )


def _save_mentorship_session(request):
    """Validasi dan simpan satu rekod MentorshipSession."""
    participant_role = _required_choice(
        request.POST.get('mentor_role'),
        MENTORSHIP_ROLES,
        'Participant Role',
    )
    mentee_name = _required_text(
        request.POST.get('mentee_name'),
        'Mentee / Officer Name',
        max_length=150,
    )
    topic = _required_choice(
        request.POST.get('mentor_domain'),
        MENTORSHIP_DOMAINS,
        'Guidance Domain',
    )
    session_date = _required_date(
        request.POST.get('mentor_date'),
        'Session Date',
    )
    duration_hours = _mentor_duration_hours(
        request.POST.get('mentor_duration'),
    )
    notes = _required_text(
        request.POST.get('mentor_notes'),
        'Action Plan & Discussion Summary',
    )

    with transaction.atomic():
        return MentorshipSession.objects.create(
            mentor=request.user,
            participant_role=participant_role,
            mentee_name=mentee_name,
            topic=topic,
            notes=notes,
            session_date=session_date,
            duration_hours=duration_hours,
        )


def _save_productivity_log(request):
    """Validasi dan simpan satu rekod productivity operasi."""
    record_date = _required_date(request.POST.get('prod_date'), 'Log Date')
    patients_cleared = _required_int(
        request.POST.get('outpatient_count'),
        'Outpatient Consults Cleared',
        minimum=0,
    )
    ward_cases = _optional_int(
        request.POST.get('ward_count'),
        'Inpatient Ward Cases',
        minimum=0,
    )
    procedures_completed = _optional_int(
        request.POST.get('procedure_count'),
        'Procedures Completed',
        minimum=0,
    )
    avg_consult_minutes = _optional_float(
        request.POST.get('avg_consult'),
        'Avg Consult Time',
    )
    if avg_consult_minutes is None:
        avg_consult_minutes = 12.0
    elif avg_consult_minutes < 0:
        raise ValidationError('Avg Consult Time tidak boleh negatif.')

    punctuality_status = _required_choice(
        request.POST.get('punctuality'),
        PRODUCTIVITY_PUNCTUALITY_RATES,
        'Clinic Start Punctuality',
    )
    punctuality_rate = PRODUCTIVITY_PUNCTUALITY_RATES[punctuality_status]
    operational_notes = _optional_text(request.POST.get('prod_notes'))

    with transaction.atomic():
        return ProductivityLog.objects.create(
            officer=request.user,
            record_date=record_date,
            patients_cleared=patients_cleared,
            ward_cases=ward_cases,
            procedures_completed=procedures_completed,
            avg_consult_minutes=avg_consult_minutes,
            punctuality_rate=punctuality_rate,
            punctuality_status=punctuality_status,
            operational_notes=operational_notes,
        )


def _save_innovation_project(request):
    """Validasi dan simpan satu rekod InnovationProject mengikut jenis borang."""
    innovation_type = _required_choice(
        request.POST.get('innovation_type'),
        INNOVATION_TYPES,
        'Innovation Type',
    )
    category = (
        _optional_choice(
            request.POST.get('category'),
            INNOVATION_TYPES,
            'Category',
        )
        or innovation_type
    )
    title_raw = _optional_text(request.POST.get('title'), max_length=200)

    project_title = ''
    research_role = None
    nmrr_id = None
    trial_phase = None
    recruitment_count = None
    trial_status = None
    adoption_date = None
    proficiency_level = None
    journal_name = None
    doi = None
    publication_year = None
    description_lines = []

    if innovation_type == 'Research':
        project_title = _required_text(
            request.POST.get('title'),
            'Research Title',
            max_length=200,
        )
        nmrr_id = _optional_text(request.POST.get('nmrr_id'), max_length=50)
        research_role = _optional_choice(
            request.POST.get('res_role'),
            INNOVATION_RESEARCH_ROLES,
            'Research Role',
        )
        if research_role:
            description_lines.append(f'Role: {research_role}')
        if nmrr_id:
            description_lines.append(f'NMRR: {nmrr_id}')

    elif innovation_type == 'Clinical Trial':
        project_title = _required_text(
            request.POST.get('title'),
            'CRC Log Number',
            max_length=200,
        )
        trial_phase = _optional_choice(
            request.POST.get('phase'),
            INNOVATION_TRIAL_PHASES,
            'Trial Phase',
        )
        recruitment_count = _optional_int(
            request.POST.get('recruitment_count'),
            'Patient Recruitment Number',
            minimum=0,
        )
        trial_status = _optional_choice(
            request.POST.get('trial_stat'),
            INNOVATION_TRIAL_STATUSES,
            'Recruitment Status',
        )
        if trial_phase:
            description_lines.append(f'Trial Phase: {trial_phase}')
        if recruitment_count is not None:
            description_lines.append(f'Recruitment: {recruitment_count}')
        if trial_status:
            description_lines.append(f'Trial Status: {trial_status}')

    elif innovation_type == 'Technology Adoption':
        project_title = _required_text(
            request.POST.get('title'),
            'Technology / System Name',
            max_length=200,
        )
        adoption_date = _optional_date(
            request.POST.get('adoption_date'),
            'Date of Adoption',
        )
        proficiency_level = _optional_choice(
            request.POST.get('tech_lvl'),
            INNOVATION_PROFICIENCY_LEVELS,
            'Proficiency Level',
        )
        if adoption_date:
            description_lines.append(
                f'Adoption Date: {adoption_date.isoformat()}'
            )
        if proficiency_level:
            description_lines.append(f'Proficiency: {proficiency_level}')

    else:  # Publication
        journal_name = _required_text(
            request.POST.get('journal'),
            'Name of Journal',
            max_length=200,
        )
        doi = _required_text(
            request.POST.get('doi'),
            'DOI',
            max_length=100,
        )
        publication_year = _optional_int(
            request.POST.get('year'),
            'Year Published',
            minimum=2000,
            maximum=timezone.localdate().year,
        )
        project_title = title_raw or journal_name
        description_lines.append(f'Journal: {journal_name}')
        description_lines.append(f'DOI: {doi}')
        if publication_year is not None:
            description_lines.append(f'Year: {publication_year}')

    description = (
        '\n'.join(description_lines)
        or f'Dihantar melalui Portal MyPrestasi ({innovation_type}).'
    )

    with transaction.atomic():
        return InnovationProject.objects.create(
            officer=request.user,
            project_title=project_title,
            innovation_type=innovation_type,
            category=category,
            research_role=research_role,
            nmrr_id=nmrr_id,
            trial_phase=trial_phase,
            recruitment_count=recruitment_count,
            trial_status=trial_status,
            adoption_date=adoption_date,
            proficiency_level=proficiency_level,
            journal_name=journal_name,
            doi=doi,
            publication_year=publication_year,
            description=description,
        )


CLINICAL_CPD_TARGET_POINTS = 50
CLINICAL_DOC_ONTIME_TARGET_PCT = 80


def _format_hours_minutes(hours):
    """Format decimal hours for the clinical documentation summary."""
    if hours is None:
        return None
    total_minutes = max(0, round(float(hours) * 60))
    return f"{total_minutes // 60}h {total_minutes % 60:02d}m"


def _build_clinical_context(user, doc_logs):
    """Build CPD and documentation values for the staff Clinical tab."""
    current_year = timezone.localdate().year
    claims = list(
        CPDClaim.objects.filter(officer=user, event_date__year=current_year)
        .order_by('-submitted_at', '-event_date', '-id')
    )
    approved_points = sum(
        claim.points for claim in claims if claim.status == 'APPROVED'
    )
    pending_claims = [claim for claim in claims if claim.status == 'PENDING']
    pending_points = sum(claim.points for claim in pending_claims)

    total_docs = doc_logs.count()
    ontime_docs = doc_logs.filter(is_ontime=True).count() if total_docs else 0
    average_delay = (
        doc_logs.aggregate(average_delay=Avg('delay_hours'))['average_delay']
        if total_docs else None
    )
    ontime_rate = (
        round((ontime_docs / total_docs) * 100) if total_docs else None
    )

    if not claims:
        status_label = 'Not Started'
        status_color = '#6d3ec4'
        status_background = '#f3edff'
        status_border = '#e7dff2'
    elif approved_points >= CLINICAL_CPD_TARGET_POINTS:
        status_label = 'On Track'
        status_color = '#16A34A'
        status_background = '#F0FDF4'
        status_border = '#BBF7D0'
    else:
        status_label = 'Behind Target'
        status_color = '#D97706'
        status_background = '#FFFBEB'
        status_border = '#FEF08A'

    return {
        'cpd_year': current_year,
        'cpd_claims': claims[:20],
        'cpd_claim_count': len(claims),
        'cpd_approved_points': approved_points,
        'cpd_pending_points': pending_points,
        'cpd_pending_count': len(pending_claims),
        'cpd_target_points': CLINICAL_CPD_TARGET_POINTS,
        'cpd_on_track': (
            bool(claims) and approved_points >= CLINICAL_CPD_TARGET_POINTS
        ),
        'cpd_status_label': status_label,
        'cpd_status_color': status_color,
        'cpd_status_background': status_background,
        'cpd_status_border': status_border,
        'doc_total': total_docs,
        'doc_ontime_rate': ontime_rate,
        'doc_ontime_rate_display': (
            f'{ontime_rate}%' if ontime_rate is not None else 'No data'
        ),
        'doc_avg_delay_display': _format_hours_minutes(average_delay) or 'No data',
        'doc_ontime_target_pct': CLINICAL_DOC_ONTIME_TARGET_PCT,
    }


@login_required(login_url='/login/')
def staff_dashboard_view(request):
    
    # Semakan peranan sebenar daripada profil pengguna (bukan sekadar session).
    profile = getattr(request.user, 'profile', None)
    if not profile or profile.role != 'STAFF':
        return redirect('login')

    section = request.GET.get('section', 'Dashboard')
    
    if request.method == 'POST':
        form_type = request.POST.get('form_type')
        
        # --- 1. MODUL KLINIKAL ---
        if form_type in ['skills', 'skills_cts', 'skills_fms']:
            try:
                (
                    clinical_domain,
                    proc_name,
                    score_val,
                    procedure_date,
                ) = _save_clinical_procedure(request, form_type, profile)
            except ValidationError as exc:
                messages.error(
                    request,
                    f"❌ {exc.messages[0] if exc.messages else exc}",
                )
                return redirect(f'/staff/?section={section}')

            messages.success(
                request,
                f"✅ Rekod klinikal {clinical_domain} '{proc_name}' "
                f"({score_val:.0f}%) untuk "
                f"{procedure_date.strftime('%d %b %Y')} disimpan.",
            )

        elif form_type == 'cpd':
            mata = max(0, _parse_int(request.POST.get('mata'), 0))
            tajuk = ((request.POST.get('tajuk') or '').strip() or 'Program CME')[:200]
            category = (request.POST.get('category') or '').strip() or 'CME Workshop'
            event_date = _parse_date_value(request.POST.get('start_date'), date.today())
            end_date_raw = (request.POST.get('end_date') or '').strip()
            end_date = _parse_date_value(end_date_raw, None)
            certificate = request.FILES.get('proof')

            if end_date_raw and end_date is None:
                messages.error(request, "❌ Format End Date tidak sah.")
                return redirect(f'/staff/?section={section}')

            if end_date and end_date < event_date:
                messages.error(
                    request,
                    "❌ End Date tidak boleh sebelum Start Date.",
                )
                return redirect(f'/staff/?section={section}')

            if certificate:
                try:
                    _validate_cpd_certificate(certificate)
                except ValidationError as exc:
                    messages.error(request, f"❌ {exc.messages[0]}")
                    return redirect(f'/staff/?section={section}')

            CPDClaim.objects.create(
                officer=request.user,
                topic=tajuk,
                points=mata,
                category=category,
                event_date=event_date,
                end_date=end_date,
                certificate=certificate or None,
                status='PENDING',
            )
            messages.success(
                request,
                f"✅ {mata} mata CPD untuk '{tajuk}' disimpan (status: Pending Verification)."
            )

        elif form_type == 'doc':
            patient_ref = _hash_patient_ref(request.POST.get('patient_id'))
            encounter_at = _parse_datetime_value(request.POST.get('encounter_at'))
            completed_at = _parse_datetime_value(request.POST.get('completed_at'))
            reason = (request.POST.get('reason') or '').strip()
            remarks = (request.POST.get('reason_remarks') or '').strip()
            encounter_type = (
                (request.POST.get('encounter_type') or '').strip()[:50] or None
            )

            if encounter_at and completed_at and completed_at >= encounter_at:
                delay_hrs = round((completed_at - encounter_at).total_seconds() / 3600.0, 2)
            else:
                delay_hrs = max(0.0, _parse_float(request.POST.get('delay_hours'), 0.0))

            reason_text = ' — '.join(part for part in [reason, remarks] if part)
            DocumentationLog.objects.create(
                officer=request.user,
                patient_id=patient_ref,
                encounter_type=encounter_type,
                encounter_at=encounter_at,
                completed_at=completed_at,
                delay_hours=delay_hrs,
                reason_for_delay=reason_text or None,
                delay_reason_category=(reason[:100] or None),
                delay_reason_remarks=(remarks or None),
                is_ontime=(delay_hrs <= 24.0),
            )
            status_label = 'On-Time' if delay_hrs <= 24.0 else 'Delayed'
            messages.success(
                request,
                f"✅ Rekod dokumentasi {patient_ref} disimpan ({delay_hrs:.1f} jam, {status_label})."
            )

        elif form_type == 'policy':
            missing = [q for q in range(4, 21) if request.POST.get(f'q{q}') not in ('Yes', 'No')]
            if missing or request.POST.get('declaration_confirm') != 'Yes':
                messages.error(
                    request,
                    "❌ Sila jawab semua soalan 4–20 dan tandakan deklarasi akhir sebelum menghantar."
                )
                return redirect(f'/staff/?section={section}')

            ratios = {}
            selected_counts = {}
            max_counts = {}
            for q in range(1, 21):
                if q in POLICY_MULTI_OPTIONS:
                    allowed_values = POLICY_MULTI_OPTIONS[q]
                    submitted_values = request.POST.getlist(f'q{q}')
                    selected_count = len(set(submitted_values) & set(allowed_values))
                    max_count = len(allowed_values)
                    selected_counts[q] = selected_count
                    max_counts[q] = max_count
                    ratios[q] = min(1.0, selected_count / float(max_count))
                else:
                    is_yes = request.POST.get(f'q{q}') == 'Yes'
                    selected_counts[q] = 1 if is_yes else 0
                    max_counts[q] = 1
                    ratios[q] = 1.0 if is_yes else 0.0

            domain_scores = {
                key: round(
                    (sum(ratios[q] for q in questions) / len(questions)) * 100.0,
                    1,
                )
                for key, questions in POLICY_DOMAIN_QUESTIONS.items()
            }

            with transaction.atomic():
                declaration_obj = PolicyDeclaration.objects.create(
                    officer=request.user,
                    score_domain_a=domain_scores['a'],
                    score_domain_b=domain_scores['b'],
                    score_domain_c=domain_scores['c'],
                    score_domain_d=domain_scores['d'],
                    score_domain_e=domain_scores['e'],
                )
                PolicyItemResponse.objects.bulk_create([
                    PolicyItemResponse(
                        declaration=declaration_obj,
                        question_number=q,
                        is_compliant=(ratios[q] >= 1.0),
                        compliance_ratio=ratios[q],
                        selected_count=selected_counts[q],
                        max_count=max_counts[q],
                    )
                    for q in range(1, 21)
                ])

            messages.success(
                request,
                "✅ Deklarasi SOP disimpan. Skor domain: "
                f"A {domain_scores['a']:.0f}%, B {domain_scores['b']:.0f}%, "
                f"C {domain_scores['c']:.0f}%, D {domain_scores['d']:.0f}%, "
                f"E {domain_scores['e']:.0f}%."
            )

        # --- 2. MODUL OPERASIONAL ---
        elif form_type == 'ops_prod':
            try:
                prod_obj = _save_productivity_log(request)
            except ValidationError as exc:
                messages.error(
                    request,
                    f"❌ {exc.messages[0] if exc.messages else exc}",
                )
                return redirect(f'/staff/?section={section}')

            prod_date = prod_obj.record_date
            outpatient_count = prod_obj.patients_cleared
            avg_consult = prod_obj.avg_consult_minutes
            p_rate = prod_obj.punctuality_rate

            messages.success(
                request,
                f"✅ Produktiviti {prod_date.strftime('%d %b %Y')}: {outpatient_count} pesakit, "
                f"{avg_consult:.0f} minit/consulta, punctuality {p_rate:.0f}%."
            )

        elif form_type == 'ops_innov':
            try:
                project = _save_innovation_project(request)
            except ValidationError as exc:
                messages.error(
                    request,
                    f"❌ {exc.messages[0] if exc.messages else exc}",
                )
                return redirect(f'/staff/?section={section}')

            innovation_type = project.innovation_type
            title = project.project_title
            messages.success(request, f"✅ Rekod {innovation_type} '{title}' disimpan.")

        # --- 3. MODUL INTERPERSONAL ---
        elif form_type == 'int_ei':
            try:
                reflection = _save_emotional_reflection(request)
            except ValidationError as exc:
                messages.error(
                    request,
                    f"❌ {exc.messages[0] if exc.messages else exc}",
                )
                return redirect(f'/staff/?section={section}')

            messages.success(
                request,
                f"✅ Refleksi EI disimpan (SA {reflection.self_awareness_score}%, "
                f"SR {reflection.self_regulation_score}%, "
                f"Empati {reflection.empathy_score}%, "
                f"De-escalation {reflection.deescalation_score}%)."
            )

        elif form_type == 'int_team':
            peer_id = _parse_int(request.POST.get('peer_id'), 0)
            category, points_awarded = _kudos_category_points(request.POST.get('kudos_category', ''))
            message = (request.POST.get('kudos_note') or '').strip()

            receiver_user = User.objects.filter(id=peer_id, profile__role='STAFF').first()
            if not receiver_user:
                messages.error(request, "❌ Rakan sekerja tidak sah. Sila pilih staf daripada senarai.")
                return redirect(f'/staff/?section={section}')
            if receiver_user.id == request.user.id:
                messages.error(request, "❌ Anda tidak boleh memberi kudos kepada diri sendiri.")
                return redirect(f'/staff/?section={section}')

            PeerKudos.objects.create(
                sender=request.user,
                receiver=receiver_user,
                category=category,
                points=points_awarded,
                message=message,
            )
            receiver_name = receiver_user.get_full_name() or receiver_user.username
            messages.success(
                request,
                f"✅ {points_awarded} mata Kudos diberikan kepada Dr. {receiver_name}."
            )

        elif form_type == 'int_mentor':
            try:
                session = _save_mentorship_session(request)
            except ValidationError as exc:
                messages.error(
                    request,
                    f"❌ {exc.messages[0] if exc.messages else exc}",
                )
                return redirect(f'/staff/?section={section}')

            role_label = session.get_participant_role_display()
            messages.success(
                request,
                f"✅ Sesi mentorship {session.duration_hours:.1f} jam "
                f"({role_label}) bersama {session.mentee_name} disimpan."
            )

        elif form_type == 'system_settings':
            # Simpan tetapan ke dalam Session Django
            request.session['dark_mode'] = (request.POST.get('dark_mode') == 'on')
            request.session['language'] = request.POST.get('language')
            request.session['timezone'] = request.POST.get('timezone')
            messages.success(request, "✅ System preferences successfully updated.")
            return redirect(f'/staff/?section={section}')

        elif form_type == 'complete_coaching':
            request.session['coaching_completed'] = True
            messages.success(request, "⚡ AI Self-Coaching plan acknowledged! Your Operational Index has been upgraded to 88% (Target Achieved).")
            
        elif form_type == 'acknowledge_feedback':
            try:
                feedback_id = _required_int(
                    request.POST.get('feedback_id'),
                    'Feedback ID',
                    minimum=1,
                )
            except ValidationError as exc:
                messages.error(
                    request,
                    f"❌ {exc.messages[0] if exc.messages else exc}",
                )
                return redirect(f'/staff/?section={section}')

            fb_obj = HODFeedback.objects.filter(
                pk=feedback_id,
                receiver_staff=request.user,
            ).first()
            if not fb_obj:
                messages.error(
                    request,
                    "❌ Feedback tidak ditemui atau bukan milik anda.",
                )
                return redirect(f'/staff/?section={section}')

            if fb_obj.is_acknowledged:
                messages.info(request, "ℹ️ Feedback ini telah pun diakui.")
            else:
                fb_obj.is_acknowledged = True
                fb_obj.acknowledged_at = timezone.now()
                fb_obj.save(update_fields=['is_acknowledged', 'acknowledged_at'])
                messages.success(request, "✅ Feedback acknowledged and archived.")
                
        return redirect(f'/staff/?section={section}')

    chart_html = ""
    radar_html = "" 
    donut_html = ""  # Tambah ini
    gauge_html = ""  # Tambah ini
    area_html = ""   # Tambah ini 
    
    if section == 'Dashboard':
        # 1. DONUT CHART (Patient Acuity Mix)
        fig_donut = go.Figure(go.Pie(
            labels=["Critical (Red)", "Urgent (Yellow)", "Stable (Green)"], values=[15, 35, 50], hole=0.65,
            marker=dict(colors=["#150a35", "#6d3ec4", "#fecdd3"]), textinfo='none' # Tema Rose
        ))
        fig_donut.update_layout(height=190, margin=dict(t=0, b=0, l=0, r=0), showlegend=True, legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5, font=dict(size=10)), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
        fig_donut.add_annotation(text="<b>100%</b><br>Capacity", x=0.5, y=0.5, font_size=14, showarrow=False)
        donut_html = fig_donut.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False})

        # 2. AREA CHART (Trend Productivity)
        bulan_label = ["Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar", "Apr", "May", "Jun"]
        output_data = [35, 52, 41, 68, 58, 80, 65, 88, 75, 96]

        fig_area = go.Figure()
        fig_area.add_trace(go.Scatter(
            x=bulan_label, y=output_data, mode='lines+markers', fill='tozeroy', 
            line=dict(color='#6d3ec4', width=4, shape='spline'),
            marker=dict(size=8, color='#150a35'),
            fillcolor='rgba(190, 18, 60, 0.2)', # Tema Rose
            name='Tasks'
        ))

        fig_area.update_layout(
            height=220, margin=dict(t=10, b=0, l=0, r=10), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', 
            xaxis_title="", yaxis_title="Tasks Completed", 
            xaxis=dict(showgrid=False), yaxis=dict(showgrid=True, gridcolor='#e7dff2')
        )
        area_html = fig_area.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False})

        # 3. GAUGE CHART (Performance Index - MyPrestasi Rose & Burgundy DNA)
        fig_gauge = go.Figure(go.Indicator(
            mode="gauge+number", value=4.2, number={'font':{'size':36, 'color':'#150a35', 'family':'Inter'}},
            gauge={
                'axis': {'range': [0, 5], 'tickwidth': 1, 'tickcolor': "#150a35", 'tickfont': {'color': '#150a35', 'family': 'Inter', 'size': 11}}, 
                'bar': {'color': "#150a35", 'thickness': 0.22},
                'bgcolor': "white",
                'borderwidth': 1,
                'bordercolor': "#e7dff2",
                'steps': [
                    {'range': [0, 3], 'color': "#f3edff"}, 
                    {'range': [3, 4], 'color': "#e7dff2"}, 
                    {'range': [4, 5], 'color': "#fecdd3"}
                ],
                'threshold': {'line': {'color': "#6d3ec4", 'width': 5}, 'thickness': 0.8, 'value': 4.5}
            }
        ))
        fig_gauge.update_layout(height=220, margin=dict(t=30, b=10, l=20, r=20), paper_bgcolor='rgba(0,0,0,0)')
        gauge_html = fig_gauge.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False})

    # --- PERFORMANCE TAB (Holistic Benchmark 10 Modul - SPIDER WEB) ---
    elif section == 'Performance':
        # Dapatkan markah komprehensif staf semasa (Real Data)
        s_scores = calculate_pri_score(request.user)
        
        # Ekstrak markah khusus modul untuk graf (menggunakan fallback jika kosong)
        cpd_score = s_scores.get('score_cpd', 0)
        clin_score = s_scores.get('score_clinical', 0)
        ops_score = s_scores.get('score_operational', 0)
        interp_score = s_scores.get('score_interpersonal', 0)
        
        categories = ['Procedure Log', 'CPD Points', 'Doc. Audit', 'Policies', 'Productivity', 'Innovation', 'Patient Exp.', 'Emotional Intel.', 'Teamwork', 'Mentorship']
        # Anggaran taburan berdasarkan tunjang untuk memenuhkan 10 bucu spider web
        scores = [
            clin_score, cpd_score, clin_score - 5, clin_score + 2, 
            ops_score, max(0, ops_score - 15), 
            interp_score, interp_score - 5, interp_score + 5, max(0, interp_score - 20)
        ]
        
        # Cek status kelengkapan 10 modul langsung dari SQLite
        has_proc = ProcedureLog.objects.filter(officer=request.user).exists()
        has_cpd = CPDClaim.objects.filter(officer=request.user).exists()
        has_docs = DocumentationLog.objects.filter(officer=request.user).exists()
        has_policy = PolicyDeclaration.objects.filter(officer=request.user).exists()
        has_prod = ProductivityLog.objects.filter(officer=request.user).exists()
        has_innov = InnovationProject.objects.filter(officer=request.user).exists()
        has_mypsq = MyPSQFeedback.objects.filter(doctor=request.user).exists()
        has_ei = EmotionalReflection.objects.filter(officer=request.user).exists()
        has_team = PeerKudos.objects.filter(receiver=request.user).exists()
        has_mentor = MentorshipSession.objects.filter(mentor=request.user).exists()

        # Hitung jumlah modul yang telah memiliki rekod data
        completed_modules_count = sum([
            has_proc, has_cpd, has_docs, has_policy, has_prod,
            has_innov, has_mypsq, has_ei, has_team, has_mentor
        ])
        portfolio_compliance_pct = int((completed_modules_count / 10.0) * 100)

        # Nilai ringkasan CUSUM dan Policy
        cusum_pct = clin_score
        policy_pct = min(100, clin_score + 5)
        
        kategori_penuh = categories + [categories[0]]
        markah_penuh = scores + [scores[0]]

        fig_radar = go.Figure()
        fig_radar.add_trace(go.Scatterpolar(
            r=markah_penuh, theta=kategori_penuh, fill='toself',
            fillcolor='rgba(190, 18, 60, 0.25)', # Warna Merah Rose Lutsinar
            line=dict(color='#150a35', width=3), # Garisan Burgundy
            marker=dict(size=8, color='#6d3ec4', symbol='circle'),
            hoverinfo='r+theta', name='My Score'
        ))
        
        fig_radar.update_layout(
            polar=dict(
                bgcolor='rgba(255, 241, 242, 0.5)', # Latar belakang bulatan Rose cair
                radialaxis=dict(visible=True, range=[0, 100], showgrid=True, gridcolor='#fecdd3', tickfont=dict(size=10, color='#150a35'), angle=90),
                angularaxis=dict(showgrid=True, gridcolor='#fecdd3', linecolor='#fecdd3', tickfont=dict(size=11, color='#150a35', weight='bold'))
            ), 
            showlegend=False, height=380, margin=dict(t=40, b=40, l=60, r=60), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)'
        )
        radar_html = fig_radar.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False})

    # --- PROFIL DINAMIK SEBENAR DARI PANGKALAN DATA ---
    user_obj = request.user
    full_name_str = f"{user_obj.first_name} {user_obj.last_name}".strip()
    
    # Jika nama penuh kosong, guna ID Pengguna sebagai nama
    staff_name = f"Dr. {full_name_str}" if full_name_str else f"Dr. {user_obj.username}"
    
    # Dapatkan jabatan dari Profil Pengguna.
    # staff_dept = kod (CTS/FMS) untuk logik template; staff_dept_display = label penuh.
    if hasattr(user_obj, 'profile') and user_obj.profile.department:
        staff_dept = user_obj.profile.department
        staff_dept_display = user_obj.profile.get_department_display()
    else:
        staff_dept = "FMS"
        staff_dept_display = "Family Medicine Specialist (FMS)"

    # ==========================================
    # INTEGRASI MyPSQ REAL-TIME (FASA 3)
    # ==========================================
    # Pengguna telah disahkan oleh @login_required + semakan profil di atas.
    staff_user = request.user

    doctor_username = staff_user.username if staff_user else 'admin'
    qr_eval_url = request.build_absolute_uri(f'/evaluate/{doctor_username}/')

    # Tarik data maklum balas MyPSQ sebenar dari DB
    feedbacks = MyPSQFeedback.objects.filter(doctor=staff_user).order_by('-created_at') if staff_user else []
    mypsq_count = feedbacks.count() if hasattr(feedbacks, 'count') else len(feedbacks)

    if mypsq_count > 0:
        avg_q = sum([f.average_score for f in feedbacks]) / mypsq_count
        mypsq_avg_score = round(avg_q, 1)
        mypsq_pct = int((avg_q / 5.0) * 100)
        mypsq_comments = [f for f in feedbacks if f.patient_comments]
    else:
        # Fallback mesra ujian jika pangkalan data belum mempunyai jawapan
        mypsq_avg_score = 4.8
        mypsq_pct = 96
        mypsq_count = 14
        mypsq_comments = []

    # ==========================================
    # PROMOTION READINESS INDEX (PRI) CALCULATION (LIVE DB INTEGRATION)
    # ==========================================
    # Tarik data dari Enjin Pengira Skor (services.py) berdasarkan data sebenar!
    scores = calculate_pri_score(request.user)
    score_clinical = scores.get('score_clinical', 85)
    score_ops_coding = scores.get('score_operational', 80)
    score_interpersonal = scores.get('score_interpersonal', 88)
    score_cpd = scores.get('score_cpd', 90)
    pri_score = scores.get('pri_score', 85)
    promotion_tier = scores.get('promotion_tier', 'Tier 1: Fast-Track Candidate')
    promotion_color = scores.get('promotion_color', '#16a34a')
    next_grade = scores.get('next_grade', 'UD48 (Fast-Track)')

    # ==========================================
    # LOGIK DIAGNOSTIK AI EVALUATOR (BERDASARKAN DATA SEBENAR)
    # ==========================================
    if request.session.get('coaching_completed'):
        score_ops_coding = max(score_ops_coding, 88)  # AI Boost jika selesai coaching
        ai_risk_tier = "GREEN"
        ai_alert_title = "AI Diagnostic Alert: Remediation Completed"
        ai_alert_msg = f"Congratulations! Operational tasks ({score_ops_coding}%) have been successfully caught up. All 3 Pillars are now in optimal range."
        ai_action_text = "View Verified Status"
    else:
        # Penilaian Risiko AI secara Real-Time bergantung pada PRI Pangkalan Data
        if score_ops_coding < 65 or score_clinical < 65:
            ai_risk_tier = "RED"
            ai_alert_title = "AI Diagnostic Alert: Operational Lag Detected"
            ai_alert_msg = f"AI Engine detected an efficiency drop (Ops: {score_ops_coding}%). Your Clinical ({score_clinical}%) & Interpersonal ({score_interpersonal}%) performance are currently monitored."
            ai_action_text = "Start AI Self-Coaching"
        elif score_ops_coding < 80 or score_clinical < 80:
            ai_risk_tier = "YELLOW"
            ai_alert_title = "AI Diagnostic Alert: Moderate Attention Needed"
            ai_alert_msg = f"Your overall performance is stable, but your operational score ({score_ops_coding}%) is at a borderline level. Self-review is recommended."
            ai_action_text = "Review Guidance"
        else:
            ai_risk_tier = "GREEN"
            ai_alert_title = "AI Diagnostic Alert: Outstanding Performance"
            ai_alert_msg = f"Congratulations! All 3 Pillars are at an outstanding level (>80%). You meet the eligibility criteria for the Excellent Service Award."
            ai_action_text = "View Talent Track"

    # Tarik rekod DocumentationLog & ProductivityLog sebenar dari DB bagi staf ini
    doc_logs = DocumentationLog.objects.filter(officer=staff_user).order_by('-created_at') if staff_user else []
    prod_logs = ProductivityLog.objects.filter(officer=staff_user).order_by('-id') if staff_user else []

    clinical_context = (
        _build_clinical_context(staff_user, doc_logs)
        if staff_user and section == 'Clinical' else {}
    )

    # Kira metrik ringkasan harian secara automatik dari data DB
    if prod_logs.exists():
        prod_cleared_avg = int(sum([p.patients_cleared for p in prod_logs]) / prod_logs.count())
        prod_consult_avg = round(sum([p.avg_consult_minutes for p in prod_logs]) / prod_logs.count(), 1)
        prod_punctuality_avg = round(sum([p.punctuality_rate for p in prod_logs]) / prod_logs.count(), 0)
    else:
        prod_cleared_avg = 42
        prod_consult_avg = 12.0
        prod_punctuality_avg = 95.0
        
    # --- TARIK NOTIFIKASI RASMI HOD KE STAFF ---
    active_hod_feedbacks = HODFeedback.objects.filter(receiver_staff=request.user, is_acknowledged=False).order_by('-created_at')

    # --- JANA DATA KALENDAR DINAMIK STAFF DARI DB SEBENAR ---
    staff_calendar_events = []

    # 1. Sesi Mentorship Dijadualkan
    for m in MentorshipSession.objects.filter(mentor=request.user):
        staff_calendar_events.append({
            'title': f"Mentoring: {m.mentee_name}",
            'date': m.session_date.strftime('%Y-%m-%d'),
            'day': m.session_date.day,
            'type': 'duty',
            'desc': f"Topic: {m.topic} ({m.duration_hours}h)"
        })

    # 2. Peringatan Kelewatan Dokumentasi Pesakit
    for ddoc in DocumentationLog.objects.filter(officer=request.user, is_ontime=False):
        ev_day = ddoc.created_at.day if hasattr(ddoc, 'created_at') and ddoc.created_at else date.today().day
        ev_date = ddoc.created_at.strftime('%Y-%m-%d') if hasattr(ddoc, 'created_at') and ddoc.created_at else date.today().strftime('%Y-%m-%d')
        staff_calendar_events.append({
            'title': f"Doc Action Due: {ddoc.patient_id}",
            'date': ev_date,
            'day': ev_day,
            'type': 'pending',
            'desc': f"Delayed entry ({ddoc.delay_hours}h)"
        })

    # 3. Maklum Balas HOD Rasmi Yang Perlu Diperakui
    for fb in active_hod_feedbacks:
        ev_day = fb.created_at.day if hasattr(fb, 'created_at') and fb.created_at else date.today().day
        ev_date = fb.created_at.strftime('%Y-%m-%d') if hasattr(fb, 'created_at') and fb.created_at else date.today().strftime('%Y-%m-%d')
        staff_calendar_events.append({
            'title': f"HOD Notice: {fb.module_reference}",
            'date': ev_date,
            'day': ev_day,
            'type': 'fast-track',
            'desc': fb.message_text[:40] + "..."
        })

    calendar_events_json = json.dumps(staff_calendar_events, cls=DjangoJSONEncoder)

    # Senarai rakan sekerja sebenar untuk dropdown Teamwork (kecuali diri sendiri).
    peer_staff_list = User.objects.filter(profile__role='STAFF').exclude(
        id=request.user.id
    ).order_by('first_name', 'last_name', 'username')

    context = {
        **clinical_context,
        'section': section,
        'calendar_events_json': calendar_events_json,
        'active_hod_feedbacks': active_hod_feedbacks,
        'doc_logs': doc_logs,
        'prod_logs': prod_logs,
        'prod_cleared_avg': prod_cleared_avg,
        'prod_consult_avg': prod_consult_avg,
        'prod_punctuality_avg': prod_punctuality_avg,
        'chart_html': chart_html,
        'radar_html': radar_html,
        'donut_html': donut_html,
        'gauge_html': gauge_html,
        'area_html': area_html,
        'today_date': date.today().strftime('%d %B %Y'),
        'clinical_score': score_clinical,
        'operational_score': score_ops_coding,
        'interpersonal_rating': "High",
        'staff_name': staff_name,
        'staff_dept': staff_dept,
        'staff_dept_display': staff_dept_display,
        'peer_staff_list': peer_staff_list,
        'has_proc': locals().get('has_proc', False),
        'has_cpd': locals().get('has_cpd', False),
        'has_docs': locals().get('has_docs', False),
        'has_policy': locals().get('has_policy', False),
        'has_prod': locals().get('has_prod', False),
        'has_innov': locals().get('has_innov', False),
        'has_mypsq': locals().get('has_mypsq', False),
        'has_ei': locals().get('has_ei', False),
        'has_team': locals().get('has_team', False),
        'has_mentor': locals().get('has_mentor', False),
        'completed_modules_count': locals().get('completed_modules_count', 0),
        'portfolio_compliance_pct': locals().get('portfolio_compliance_pct', 0),
        'cusum_pct': locals().get('cusum_pct', 85),
        'policy_pct': locals().get('policy_pct', 90),
        'ai_risk_tier': ai_risk_tier,
        'ai_alert_title': ai_alert_title,
        'ai_alert_msg': ai_alert_msg,
        'ai_action_text': ai_action_text,
        'score_clinical': score_clinical,
        'score_ops_coding': score_ops_coding,
        'score_interpersonal': score_interpersonal,
        'pri_score': pri_score,
        'promotion_tier': promotion_tier,
        'promotion_color': promotion_color,
        'next_grade': next_grade,
        
        # --- DATA MyPSQ UNTUK PORTAL STAF ---
        'qr_eval_url': qr_eval_url,
        'doctor_username': doctor_username,
        'mypsq_avg_score': mypsq_avg_score,
        'mypsq_pct': mypsq_pct,
        'mypsq_count': mypsq_count,
        'mypsq_feedbacks': feedbacks,
        'mypsq_comments': mypsq_comments,
    }
    return render(request, 'dashboard/staff_dashboard.html', context)


from django.contrib.auth.models import User
from .models import UserProfile
from django.http import HttpResponse

@login_required(login_url='/login/')
def hpu_dashboard(request):
    """
    Pusat Kawalan HPU: Pengurusan Pengguna, Agihan Fasiliti, & Selenggara Peranan
    """
    if request.session.get('role') != 'admin_hpu':
        return redirect('login')

    if request.method == 'POST':
        form_type = request.POST.get('form_type')

        # 1. CIPTA PENGGUNA BAHARU DARI DASHBOARD HPU
        if form_type == 'create_user':
            username = request.POST.get('username', '').strip()
            password = request.POST.get('password', '').strip()
            role = request.POST.get('role', 'STAFF')
            department = request.POST.get('department', 'FMS')
            facility = request.POST.get('facility', 'Klinik Kesihatan Medong').strip()
            state = request.POST.get('state', 'Sarawak').strip()

            valid_scopes = {choice[0] for choice in UserProfile.HOD_SCOPE_CHOICES}
            scope = request.POST.get('scope', 'FACILITY')
            if scope not in valid_scopes:
                scope = 'FACILITY'

            if role == 'HOD':
                if scope in ('FACILITY', 'STATE') and not state:
                    messages.error(request, "Error: Skop HOD State/Facility memerlukan State.")
                    return redirect('hpu_dashboard')
                if scope == 'FACILITY' and not facility:
                    messages.error(request, "Error: Skop HOD Fasiliti memerlukan Facility/Clinic.")
                    return redirect('hpu_dashboard')

            if User.objects.filter(username=username).exists():
                messages.error(request, f"Error: User ID '{username}' already exists in database.")
            else:
                new_user = User.objects.create_user(username=username, password=password)
                UserProfile.objects.create(
                    user=new_user,
                    role=role,
                    department=department,
                    assigned_facility=facility,
                    assigned_state=state,
                    hod_scope=scope if role == 'HOD' else 'NATIONAL'
                )
                messages.success(request, f"✅ User account '{username}' successfully registered and assigned to {facility}.")
            return redirect('hpu_dashboard')

        # 2. KEMAS KINI PROFIL PENGGUNA (ROLE, DEPT, FASILITI)
        elif form_type == 'edit_user':
            original_username = request.POST.get('original_username', '').strip()
            target_user = User.objects.filter(username=original_username).first()

            if target_user:
                new_role = request.POST.get('role', 'STAFF')
                new_dept = request.POST.get('department', 'FMS')
                new_facility = request.POST.get('facility', 'Klinik Kesihatan Medong').strip()
                new_state = request.POST.get('state', 'Sarawak').strip()

                valid_scopes = {choice[0] for choice in UserProfile.HOD_SCOPE_CHOICES}
                new_scope = request.POST.get('scope', 'FACILITY')
                if new_scope not in valid_scopes:
                    new_scope = 'FACILITY'

                if new_role == 'HOD':
                    if new_scope in ('FACILITY', 'STATE') and not new_state:
                        messages.error(request, "Error: Skop HOD State/Facility memerlukan State.")
                        return redirect('hpu_dashboard')
                    if new_scope == 'FACILITY' and not new_facility:
                        messages.error(request, "Error: Skop HOD Fasiliti memerlukan Facility/Clinic.")
                        return redirect('hpu_dashboard')

                profile, _ = UserProfile.objects.get_or_create(user=target_user)
                profile.role = new_role
                profile.department = new_dept
                profile.assigned_facility = new_facility
                profile.assigned_state = new_state
                profile.hod_scope = new_scope if new_role == 'HOD' else 'NATIONAL'
                profile.save()

                messages.success(request, f"✅ Profile for '{original_username}' updated ({new_role} • {new_facility}).")
            else:
                messages.error(request, f"Error: User '{original_username}' not found.")
            return redirect('hpu_dashboard')

        # 3. PADAM PENGGUNA
        elif form_type == 'delete_user':
            target_username = request.POST.get('target_username', '').strip()
            if target_username != request.user.username:
                User.objects.filter(username=target_username).delete()
                messages.success(request, f"🗑️ User account '{target_username}' has been deleted.")
            else:
                messages.error(request, "Error: You cannot delete your own administrative account.")
            return redirect('hpu_dashboard')

    # Dapatkan senarai semua profil pengguna
    all_users = UserProfile.objects.all().select_related('user').order_by('-id')

    context = {
        'users': all_users,
        'senarai_negeri': list(STATE_CENTERS.keys()),
        'senarai_klinik': [k['name'] for k in MOCK_KLINIK],
        'hod_scope_choices': UserProfile.HOD_SCOPE_CHOICES,
    }
    return render(request, 'dashboard/hpu_dashboard.html', context)

# ==========================================
# OVERALL ANALYTICS (DRILL-DOWN FASA 1)
# ==========================================
@login_required(login_url='/login/')
def overall_analytics_view(request):
    """
    Paparan khas untuk perincian mendalam 4 Kad Overall Statistics.
    Menerima parameter URL '?type=facilities|staff|performance|evaluations'
    """
    analytic_type = request.GET.get('type', 'facilities')

    # === LOGIK SKOP HOD ===
    hod_scope = getattr(request.user.profile, 'hod_scope', 'NATIONAL')
    hod_state = getattr(request.user.profile, 'assigned_state', None)
    hod_facility = getattr(request.user.profile, 'assigned_facility', None)

    # --- TAPIS NEGERI ---
    senarai_negeri = list(STATE_CENTERS.keys())
    if hod_scope in ['FACILITY', 'STATE'] and hod_state:
        pilihan_negeri = hod_state
    else:
        pilihan_negeri = request.GET.get('state', 'All States')

    if pilihan_negeri == "All States":
        klinik_dipaparkan = MOCK_KLINIK
    else:
        klinik_dipaparkan = [k for k in MOCK_KLINIK if k.get("state") == pilihan_negeri]

    # --- TAPIS KLINIK ---
    senarai_klinik = [k['name'] for k in klinik_dipaparkan]
    if hod_scope == 'FACILITY' and hod_facility:
        pilihan_klinik = hod_facility
        klinik_dipaparkan = [k for k in klinik_dipaparkan if k.get("name") == pilihan_klinik]
    else:
        pilihan_klinik = request.GET.get('clinic', 'All Clinics')
        if pilihan_klinik != 'All Clinics' and pilihan_klinik in senarai_klinik:
            klinik_dipaparkan = [k for k in klinik_dipaparkan if k.get("name") == pilihan_klinik]
        else:
            pilihan_klinik = 'All Clinics'

    is_facility_locked = (hod_scope == 'FACILITY')
    is_state_locked = (hod_scope == 'STATE')
    
    hod_name = f"{request.user.first_name} {request.user.last_name}".strip() or request.user.username
    hod_facility_label = hod_facility if hod_scope == 'FACILITY' and hod_facility else "HPU Putrajaya"

    # --- KUMPUL DATA MENTAH UNTUK STATISTIK & JADUAL PERINCIAN ---
    active_facilities = len(klinik_dipaparkan) if pilihan_klinik == 'All Clinics' else 1
    semua_markah = []
    senarai_doktor_lengkap = []
    
    for k in klinik_dipaparkan:
        df_docs = generate_mock_doctors(k['name'])
        for _, doc in df_docs.iterrows():
            senarai_doktor_lengkap.append({
                'name': doc['Officer Name'],
                'clinic': k['name'],
                'state': k.get('state', pilihan_negeri),
                'score': doc['Current Score'],
                'status': doc['Status']
            })
            semua_markah.append(doc['Current Score'])
            
    total_staff = len(senarai_doktor_lengkap)
    avg_performance = int(sum(semua_markah) / len(semua_markah)) if semua_markah else 0
    
    import random
    random.seed(total_staff)
    evaluations_completed = total_staff * random.randint(3, 6)

    context = {
        'analytic_type': analytic_type,
        'senarai_negeri': senarai_negeri,
        'pilihan_negeri': pilihan_negeri,
        'senarai_klinik': ["All Clinics"] + senarai_klinik,
        'pilihan_klinik': pilihan_klinik,
        'is_facility_locked': is_facility_locked,
        'is_state_locked': is_state_locked,
        'hod_scope': hod_scope,
        'hod_name': hod_name,
        'hod_facility_label': hod_facility_label,
        
        # Data Sidebar
        'active_facilities': active_facilities,
        'total_staff': total_staff,
        'avg_performance': avg_performance,
        'evaluations_completed': evaluations_completed,
        
        # Data Mentah Untuk Dibedah di Fasa 2 (HTML)
        'klinik_dipaparkan': klinik_dipaparkan,
        'senarai_doktor_lengkap': senarai_doktor_lengkap,
    }

    return render(request, 'dashboard/overall_analytics.html', context)

# ==========================================
# DEDICATED OFFICER COGNITIVE PROFILE (10-MODULE RADAR & DB INTEGRATED)
# ==========================================
@login_required(login_url='/login/')
def officer_profile_view(request):
    officer_name = request.GET.get('officer', 'Pegawai Perubatan')
    clinic_name = request.GET.get('clinic', 'Klinik Kesihatan Medong')
    state_name = request.GET.get('state', 'Sarawak')
    
    # 1. Bersihkan Nama Pegawai untuk Semakan Pangkalan Data Sebenar
    clean_name = officer_name.replace("🟢", "").replace("Dr.", "").split(" (")[0].strip()
    target_user = User.objects.filter(username__icontains=clean_name).first()
    if not target_user:
        target_user = User.objects.filter(first_name__icontains=clean_name).first()
        
    # 2. Tarik Data Sebenar / Fallback Terkawal Berasaskan Nama
    if target_user:
        scores = calculate_pri_score(target_user)
        score_clinical = scores.get('score_clinical', 85)
        score_ops = scores.get('score_operational', 80)
        score_interp = scores.get('score_interpersonal', 88)
        score_cpd = scores.get('score_cpd', 90)
        total_score = scores.get('pri_score', 85)
        promotion_tier = scores.get('promotion_tier', 'Tier 1: Fast-Track Candidate')
        grade_label = target_user.profile.get_department_display() if hasattr(target_user, 'profile') else 'Medical Officer'
    else:
        import random
        random.seed(officer_name)
        score_clinical = random.randint(78, 94)
        score_ops = random.randint(75, 92)
        score_interp = random.randint(80, 95)
        score_cpd = random.randint(80, 98)
        total_score = int((0.40 * score_clinical) + (0.30 * score_ops) + (0.30 * score_interp))
        promotion_tier = "Tier 1: Fast-Track Candidate" if total_score >= 85 else ("Tier 2: On-Track (Time-Based)" if total_score >= 70 else "Tier 3: Action Required")
        grade_label = "UD43/47/52"

    status = "Outstanding" if total_score >= 85 else ("Competent" if total_score >= 70 else "Needs Attention")

    # 3. Nilai 10 Modul Lengkap untuk Radar 360°
    categories = [
        'Procedure Log', 'CPD Points', 'Policies (SOP)', 'Doc. Audit', 
        'Productivity', 'Innovation', 
        'Patient Exp.', 'Emotional Intel.', 'Teamwork', 'Mentorship'
    ]
    values = [
        score_clinical,
        score_cpd,
        min(100, score_clinical + 3),
        max(65, score_clinical - 4),
        score_ops,
        max(60, score_ops - 8),
        score_interp,
        max(70, score_interp - 4),
        min(100, score_interp + 4),
        max(65, score_interp - 6)
    ]

    # 4. Bina Radar Chart 10 Modul Bertemakan Royal Purple
    fig_radar = go.Figure()
    fig_radar.add_trace(go.Scatterpolar(
        r=values + [values[0]],
        theta=categories + [categories[0]],
        fill='toself',
        fillcolor='rgba(109, 62, 196, 0.22)',
        line=dict(color='#6d3ec4', width=3),
        marker=dict(size=7, color='#150a35', symbol='circle'),
        hoverinfo='r+theta',
        name='Competency'
    ))
    fig_radar.update_layout(
        polar=dict(
            bgcolor='rgba(243, 237, 255, 0.45)',
            radialaxis=dict(visible=True, range=[0, 100], showgrid=True, gridcolor='#e7dff2', tickfont=dict(size=9.5, color='#150a35')),
            angularaxis=dict(showgrid=True, gridcolor='#e7dff2', linecolor='#e7dff2', tickfont=dict(size=10.5, color='#150a35', weight='bold'))
        ),
        margin=dict(t=25, b=25, l=45, r=45),
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        showlegend=False,
        height=380
    )
    radar_html = fig_radar.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False})

    promotion_track = promotion_tier.split(":")[0] if ":" in str(promotion_tier) else promotion_tier

    context = {
        'officer_name': officer_name,
        'clinic_name': clinic_name,
        'state_name': state_name,
        'total_score': total_score,
        'status': status,
        'promotion_tier': promotion_tier,
        'promotion_track': promotion_track,
        'grade_label': grade_label,
        'score_clinical': score_clinical,
        'score_ops': score_ops,
        'score_interp': score_interp,
        'score_cpd': score_cpd,
        'radar_html': radar_html,
    }
    return render(request, 'dashboard/officer_profile.html', context)


# ==========================================
# 4 HALAMAN KHUSUS (DEDICATED INSIGHT VIEWS)
# ==========================================

@login_required(login_url='/login/')
def action_center_insight_view(request):
    """
    Dedicated Insight Page: HOD Action Center (Red Flags & Triage)
    """
    hod_scope = getattr(request.user.profile, 'hod_scope', 'NATIONAL')
    hod_state = getattr(request.user.profile, 'assigned_state', None)
    hod_facility = getattr(request.user.profile, 'assigned_facility', None)

    senarai_negeri = list(STATE_CENTERS.keys())
    pilihan_negeri = hod_state if (hod_scope in ['FACILITY', 'STATE'] and hod_state) else request.GET.get('state', 'All States')

    if pilihan_negeri == "All States":
        klinik_dipaparkan = MOCK_KLINIK
    else:
        klinik_dipaparkan = [k for k in MOCK_KLINIK if k.get("state") == pilihan_negeri]

    senarai_klinik = [k['name'] for k in klinik_dipaparkan]
    pilihan_klinik = hod_facility if (hod_scope == 'FACILITY' and hod_facility) else request.GET.get('clinic', 'All Clinics')

    hod_name = f"{request.user.first_name} {request.user.last_name}".strip() or request.user.username

    red_flags = [
        {'type': 'CUSUM Breach Warning', 'officer': 'Dr. Ramesh Lee', 'facility': 'Klinik Kesihatan Medong', 'severity': 'Urgent', 'time': 'Today, 08:30 AM', 'detail': 'Safety control limit breached in LIMA-LAD Procedure.'},
        {'type': 'Fast-Track Candidates (PRI ≥ 85%)', 'officer': '24 Staffs', 'facility': 'Multiple Facilities', 'severity': 'Success', 'time': 'Today, 07:00 AM', 'detail': 'AI shortlisted 24 Officers eligible for Fast-Track UD48 promotion.'},
        {'type': 'Pending Approval Log', 'officer': 'Multiple Officers', 'facility': 'Klinik Kesihatan Medong', 'severity': 'Pending', 'time': 'Yesterday, 18:00 PM', 'detail': '8 Procedure logs & 3 CPD claims require HOD validation.'},
        {'type': 'SOP Documentation Drop', 'officer': 'Outpatient Department', 'facility': 'Klinik Kesihatan Medong', 'severity': 'Warning', 'time': '12 Sep 2026', 'detail': 'Clinic Outpatient records timeliness dropped below 80% target.'},
    ]

    context = {
        'senarai_negeri': senarai_negeri,
        'pilihan_negeri': pilihan_negeri,
        'senarai_klinik': ["All Clinics"] + senarai_klinik,
        'pilihan_klinik': pilihan_klinik,
        'hod_name': hod_name,
        'red_flags': red_flags,
        'dept': getattr(request.user.profile, 'department', 'FMS'),
    }
    return render(request, 'dashboard/action_center_insight.html', context)


@login_required(login_url='/login/')
def kpi_analytics_insight_view(request):
    """
    Dedicated Insight Page: Department Monthly KPI Deep-Dive
    """
    hod_scope = getattr(request.user.profile, 'hod_scope', 'NATIONAL')
    hod_state = getattr(request.user.profile, 'assigned_state', None)
    hod_facility = getattr(request.user.profile, 'assigned_facility', None)

    senarai_negeri = list(STATE_CENTERS.keys())
    pilihan_negeri = hod_state if (hod_scope in ['FACILITY', 'STATE'] and hod_state) else request.GET.get('state', 'All States')

    if pilihan_negeri == "All States":
        klinik_dipaparkan = MOCK_KLINIK
    else:
        klinik_dipaparkan = [k for k in MOCK_KLINIK if k.get("state") == pilihan_negeri]

    senarai_klinik = [k['name'] for k in klinik_dipaparkan]
    pilihan_klinik = hod_facility if (hod_scope == 'FACILITY' and hod_facility) else request.GET.get('clinic', 'All Clinics')

    hod_name = f"{request.user.first_name} {request.user.last_name}".strip() or request.user.username

    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    target_trend = [85]*12
    actual_trend = [82, 84, 86, 88, 85, 89, 91, 90, 88, 92, 94, 93]

    fig_trend = go.Figure()
    fig_trend.add_trace(go.Scatter(
        x=months, y=target_trend, mode='lines', name="Target KPI (85%)",
        line=dict(color="#a577ff", width=2, dash='dash')
    ))
    fig_trend.add_trace(go.Scatter(
        x=months, y=actual_trend, mode='lines+markers', name="Actual Performance",
        line=dict(color="#150a35", width=3), marker=dict(size=8, color="#6d3ec4"),
        fill='tozeroy', fillcolor='rgba(190, 18, 60, 0.08)'
    ))
    fig_trend.update_layout(
        height=320, margin=dict(l=30, r=20, t=20, b=30),
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(size=11, color='#150a35', family='Inter')),
        xaxis=dict(showgrid=False, tickfont=dict(size=11, color='#150a35', weight='bold', family='Inter')),
        yaxis=dict(showgrid=True, gridcolor='#e7dff2', range=[60, 100], tickfont=dict(size=10, color='#150a35', family='Inter'))
    )
    trend_chart_html = fig_trend.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False})

    context = {
        'senarai_negeri': senarai_negeri,
        'pilihan_negeri': pilihan_negeri,
        'senarai_klinik': ["All Clinics"] + senarai_klinik,
        'pilihan_klinik': pilihan_klinik,
        'hod_name': hod_name,
        'trend_chart_html': trend_chart_html,
        'dept': getattr(request.user.profile, 'department', 'FMS'),
    }
    return render(request, 'dashboard/kpi_insight.html', context)


@login_required(login_url='/login/')
def competency_matrix_insight_view(request):
    """
    Dedicated Insight Page: MO Competency Matrix & PRI Distribution (Optimized)
    """
    hod_scope = getattr(request.user.profile, 'hod_scope', 'NATIONAL')
    hod_state = getattr(request.user.profile, 'assigned_state', None)
    hod_facility = getattr(request.user.profile, 'assigned_facility', None)

    pilihan_negeri = hod_state if (hod_scope in ['FACILITY', 'STATE'] and hod_state) else request.GET.get('state', 'All States')
    pilihan_klinik = hod_facility if (hod_scope == 'FACILITY' and hod_facility) else request.GET.get('clinic', 'All Clinics')

    hod_name = f"{request.user.first_name} {request.user.last_name}".strip() or request.user.username

    # Tarik data staf dari DB dan hadkan kepada 10 rekod terawal untuk kepantasan
    staff_users = User.objects.filter(profile__role='STAFF')[:10]
    
    mo_names = []
    z_data = []

    if staff_users.exists():
        for staff in staff_users:
            fullname = f"Dr. {staff.first_name} {staff.last_name}".strip()
            mo_names.append(fullname if fullname != "Dr." else f"Dr. {staff.username}")
            
            try:
                # Cuba kira skor menggunakan fungsi sedia ada
                scores = calculate_pri_score(staff)
                z_data.append([
                    scores.get('score_clinical', 85), 
                    scores.get('score_operational', 80), 
                    scores.get('score_interpersonal', 88)
                ])
            except Exception as e:
                # Fallback selamat jika calculate_pri_score gagal
                z_data.append([85, 80, 88])
    else:
        # Fallback jika tiada staf langsung dalam DB
        mo_names = ["Dr. Ramesh Lee", "Dr. Siti Abu", "Dr. Chong Ali", "Dr. Mei Ismail", "Dr. Mohammad"]
        z_data = [[88, 92, 78], [65, 70, 82], [94, 85, 90], [72, 68, 75], [85, 88, 92]]

    tunjang_labels = ['Clinical', 'Operational', 'Interpersonal']

    fig_heat = go.Figure(data=go.Heatmap(
        z=z_data, x=tunjang_labels, y=mo_names,
        colorscale=[[0, '#f3edff'], [0.4, '#fecdd3'], [0.7, '#a577ff'], [1.0, '#6d3ec4']],
        texttemplate="<b>%{z}%</b>", textfont=dict(size=11, color="#ffffff", family='Inter'),
        showscale=False, xgap=5, ygap=5,
        hovertemplate="<b>Pegawai:</b> %{y}<br><b>Tunjang:</b> %{x}<br><b>Skor:</b> %{z}%<extra></extra>"
    ))
    fig_heat.update_layout(
        height=380, margin=dict(l=120, r=20, t=20, b=30),
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
        xaxis=dict(fixedrange=True, tickfont=dict(size=11, color='#150a35', weight='bold', family='Inter')),
        yaxis=dict(fixedrange=True, tickfont=dict(size=10, color='#150a35', weight='bold', family='Inter'), autorange="reversed")
    )
    heatmap_html = fig_heat.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False})

    context = {
        'senarai_negeri': list(STATE_CENTERS.keys()),
        'pilihan_negeri': pilihan_negeri,
        'senarai_klinik': ["All Clinics", pilihan_klinik] if pilihan_klinik != "All Clinics" else ["All Clinics"],
        'pilihan_klinik': pilihan_klinik,
        'hod_name': hod_name,
        'heatmap_html': heatmap_html,
        'dept': getattr(request.user.profile, 'department', 'FMS'),
    }
    return render(request, 'dashboard/competency_insight.html', context)


@login_required(login_url='/login/')
def officers_directory_insight_view(request):
    """
    Dedicated Insight Page: Staffs Directory & Roster Allocation
    """
    hod_scope = getattr(request.user.profile, 'hod_scope', 'NATIONAL')
    hod_state = getattr(request.user.profile, 'assigned_state', None)
    hod_facility = getattr(request.user.profile, 'assigned_facility', None)

    senarai_negeri = list(STATE_CENTERS.keys())
    pilihan_negeri = hod_state if (hod_scope in ['FACILITY', 'STATE'] and hod_state) else request.GET.get('state', 'All States')

    if pilihan_negeri == "All States":
        klinik_dipaparkan = MOCK_KLINIK
    else:
        klinik_dipaparkan = [k for k in MOCK_KLINIK if k.get("state") == pilihan_negeri]

    senarai_klinik = [k['name'] for k in klinik_dipaparkan]
    pilihan_klinik = hod_facility if (hod_scope == 'FACILITY' and hod_facility) else request.GET.get('clinic', 'All Clinics')

    hod_name = f"{request.user.first_name} {request.user.last_name}".strip() or request.user.username

    senarai_doktor_lengkap = []
    for k in klinik_dipaparkan:
        if pilihan_klinik == 'All Clinics' or k['name'] == pilihan_klinik:
            df_docs = generate_mock_doctors(k['name'])
            for _, doc in df_docs.iterrows():
                senarai_doktor_lengkap.append({
                    'name': doc['Officer Name'],
                    'clinic': k['name'],
                    'state': k.get('state', pilihan_negeri),
                    'grade': doc['Grade'],
                    'score': doc['Current Score'],
                    'status': doc['Status']
                })

    context = {
        'senarai_negeri': senarai_negeri,
        'pilihan_negeri': pilihan_negeri,
        'senarai_klinik': ["All Clinics"] + senarai_klinik,
        'pilihan_klinik': pilihan_klinik,
        'hod_name': hod_name,
        'senarai_doktor_lengkap': senarai_doktor_lengkap,
        'dept': getattr(request.user.profile, 'department', 'FMS'),
    }
    return render(request, 'dashboard/officers_directory_insight.html', context)

# ==========================================
# FASA 1: LOGIK BORANG MyPSQ PESAKIT (PUBLIC ACCESS)
# ==========================================
from django.shortcuts import get_object_or_404
from .models import MyPSQFeedback

def patient_mypsq_view(request, doctor_username):
    """
    Paparan awam untuk pesakit mengisi borang MyPSQ bagi doktor spesifik.
    URL contoh: /evaluate/admin/ (Mesra ujian & menyokong fallback data)
    """
    # Doktor mesti wujud dan aktif. Jangan sesekali fallback kepada akaun rawak.
    doctor_username = urllib.parse.unquote(doctor_username).strip()
    doctor_obj = get_object_or_404(User, username=doctor_username, is_active=True)
    doctor_profile = getattr(doctor_obj, 'profile', None)
    doc_full_name = f"{doctor_obj.first_name} {doctor_obj.last_name}".strip() or doctor_username
    facility_name = (
        getattr(doctor_profile, 'assigned_facility', None)
        if doctor_profile else None
    ) or 'Klinik Kesihatan Medong'

    # Jika pesakit telah menekan butang Submit
    if request.method == 'POST':
        q1 = min(5, max(1, _parse_int(request.POST.get('q1'), 5)))
        q2 = min(5, max(1, _parse_int(request.POST.get('q2'), 5)))
        q3 = min(5, max(1, _parse_int(request.POST.get('q3'), 5)))
        q4 = min(5, max(1, _parse_int(request.POST.get('q4'), 5)))
        q5 = min(5, max(1, _parse_int(request.POST.get('q5'), 5)))
        q6 = min(5, max(1, _parse_int(request.POST.get('q6'), 5)))
        q7 = min(5, max(1, _parse_int(request.POST.get('q7'), 5)))
        
        comments = request.POST.get('patient_comments', '')
        pt_category = request.POST.get('patient_category', 'Outpatient')

        MyPSQFeedback.objects.create(
            doctor=doctor_obj,
            patient_category=pt_category,
            q1_general=q1,
            q2_technical=q2,
            q3_interpersonal=q3,
            q4_communication=q4,
            q5_financial=q5,
            q6_time=q6,
            q7_accessibility=q7,
            patient_comments=comments
        )
        
        messages.success(request, "Terima kasih! Maklum balas anda sangat berharga bagi kami.")
        return redirect('patient_mypsq_form', doctor_username=doctor_username)

    context = {
        'doctor_username': doctor_username,
        'doc_full_name': doc_full_name,
        'facility_name': facility_name,
    }
    return render(request, 'dashboard/patient_mypsq_form.html', context)

    