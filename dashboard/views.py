from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth.models import User
from datetime import date
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import folium
import random
import math
import hashlib
import urllib.parse

# Import Data Simulasi (Mock Data)
from .mock_data import STATE_CENTERS, MOCK_KLINIK, generate_mock_doctors, MOCK_CUSUM_DATA

# Import Enjin Pengira Skor & Model Pangkalan Data MyPrestasi
from .services import calculate_pri_score
from .models import (
    UserProfile, MyPSQFeedback, ProcedureLog, PolicyDeclaration,
    PolicyItemResponse, DocumentationLog, CPDClaim, ProductivityLog,
    InnovationProject, EmotionalReflection, PeerKudos, MentorshipSession
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

    # 1. TAMBAH BARIS INI: Tangkap isyarat tab (Default: Dashboard)
    section = request.GET.get('section', 'Dashboard')
    
    # 2. TAMBAH BARIS INI: Logik untuk paparkan mesej berjaya bila butang Save ditekan
    if request.method == 'POST':
        if request.POST.get('form_type') == 'settings':
            messages.success(request, "✅ HOD Profile & Account Settings successfully updated.")
            return redirect(request.path + '?section=Profile')
        elif request.POST.get('form_type') == 'system_settings':
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
            lambda x: f"<a href='{clinical_url}?officer={urllib.parse.quote(x)}&clinic={urllib.parse.quote(pilihan_klinik)}&state={urllib.parse.quote(pilihan_negeri)}&tab=Skills' style='color:#be123c; font-weight:800; text-decoration:none;' onmouseover='this.style.textDecoration=\"underline\"' onmouseout='this.style.textDecoration=\"none\"'>{x}</a>"
        )
        roster_html = df_docs.to_html(index=False, classes="roster-table", escape=False)
    else:
        df_docs = pd.DataFrame()
        roster_html = "<div style='text-align:center; padding: 20px; color:#a3aed1;'>No officers found for this state.</div>"

    # Dapatkan nama dinamik untuk header
    hod_name = f"{request.user.first_name} {request.user.last_name}".strip() or request.user.username
    hod_facility_label = hod_facility if hod_scope == 'FACILITY' and hod_facility else "HPU Putrajaya"

    # B. CARTA BAR KPI JABATAN (ROUNDED MODERN BARS)
    kpi_categories = ["Clinical Excellence", "CPD & Knowledge", "Doc Audit", "Policy Compliance"]
    kpi_targets = [85, 80, 90, 100]
    kpi_actuals = [88, 75, 82, 95]

    fig_kpi = go.Figure()
    fig_kpi.add_trace(go.Bar(
        x=kpi_categories, y=kpi_targets, name="Target KPI",
        marker=dict(color="#ffe4e6", line=dict(color="#f43f5e", width=1), cornerradius=8),
        text=[f"{v}%" for v in kpi_targets], textposition='auto',
        textfont=dict(color='#be123c', size=11, family='Inter', weight='bold'),
        hovertemplate="<b>%{x}</b><br>Target: %{y}%<extra></extra>"
    ))
    fig_kpi.add_trace(go.Bar(
        x=kpi_categories, y=kpi_actuals, name="Actual Score",
        marker=dict(color="#be123c", line=dict(color="#881337", width=1), cornerradius=8),
        text=[f"{v}%" for v in kpi_actuals], textposition='auto',
        textfont=dict(color='#ffffff', size=11, family='Inter', weight='bold'),
        hovertemplate="<b>%{x}</b><br>Actual: %{y}%<extra></extra>"
    ))

    fig_kpi.update_layout(
        barmode='group', height=240,
        margin=dict(l=25, r=15, t=15, b=25), bargap=0.28, bargroupgap=0.12,
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(size=10, color='#881337', family='Inter')),
        xaxis=dict(fixedrange=True, showgrid=False, tickfont=dict(size=9.5, color='#881337', weight='bold', family='Inter')),
        yaxis=dict(fixedrange=True, showgrid=True, gridcolor='#ffe4e6', range=[0, 110], tickfont=dict(size=9, color='#881337'))
    )
    dept_kpi_html = fig_kpi.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False, 'responsive': True})

    # C. MO COMPETENCY HEATMAP (GLASS & SOFT GRADIENT LOOK)
    if not df_docs.empty:
        mo_names = df_docs['Officer Name'].str.replace(r'<[^>]*>', '', regex=True).tolist()[:5]
    else:
        mo_names = ["Dr. Ramesh Lee", "Dr. Siti Abu", "Dr. Chong Ali", "Dr. Mei Ismail", "Dr. Mohammad"]

    z_mo_data = [
        [88, 92, 78],
        [65, 70, 82],
        [94, 85, 90],
        [72, 68, 75],
        [85, 88, 92]
    ]
    tunjang_labels = ['Clinical', 'Operational', 'Interpersonal']

    fig_mo_heat = go.Figure(data=go.Heatmap(
        z=z_mo_data, x=tunjang_labels, y=mo_names,
        colorscale=[[0, '#fff1f2'], [0.4, '#fecdd3'], [0.7, '#f43f5e'], [1.0, '#be123c']],
        texttemplate="<b>%{z}%</b>", textfont=dict(size=12, color="#ffffff", family='Inter'),
        showscale=False, xgap=6, ygap=6,
        hovertemplate="<b>Pegawai:</b> %{y}<br><b>Tunjang:</b> %{x}<br><b>Skor:</b> %{z}%<extra></extra>"
    ))
    fig_mo_heat.update_layout(
        autosize=True, height=250,
        margin=dict(l=10, r=10, t=10, b=25),
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
        xaxis=dict(fixedrange=True, tickfont=dict(size=10, color='#881337', weight='bold', family='Inter')),
        yaxis=dict(showticklabels=False, fixedrange=True, autorange="reversed")
    )
    mo_heatmap_html = fig_mo_heat.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False, 'responsive': True})

    # D. DYNAMIC SUNBURST FRAMEWORK (HIGH-CONTRAST PALETTE)
    data_nav = {
        "labels": ["HPU", "Clinical<br>Excellence", "Operational", "Interpersonal<br>Excellence", "Skills", "Knowledge &<br>Capacity", "Policies &<br>Protocols", "Documentation", "Productivity &<br>Efficiency", "Innovation &<br>Problem Solving", "Patient<br>Experience", "Emotional<br>Intelligence", "Teamwork &<br>Collaboration", "Mentorship"],
        "parents": ["", "HPU", "HPU", "HPU", "Clinical<br>Excellence", "Clinical<br>Excellence", "Clinical<br>Excellence", "Clinical<br>Excellence", "Operational", "Operational", "Interpersonal<br>Excellence", "Interpersonal<br>Excellence", "Interpersonal<br>Excellence", "Interpersonal<br>Excellence"],
        "colors": ["Base", "Clinical", "Operational", "Interpersonal"] + ["Clinical"]*4 + ["Operational"]*2 + ["Interpersonal"]*4
    }
    color_map = {"Base": "#ffffff", "Clinical": "#881337", "Operational": "#be123c", "Interpersonal": "#e11d48"}
    fig_sunburst = px.sunburst(pd.DataFrame(data_nav), names='labels', parents='parents', color='colors', color_discrete_map=color_map)
    fig_sunburst.update_traces(
        insidetextorientation='horizontal', textinfo='label',
        textfont=dict(size=9.5, color='#ffffff', family='Inter', weight='bold'),
        marker=dict(line=dict(color='#ffffff', width=2.5)),
        hovertemplate="<b>%{label}</b><br>Modul MyPrestasi<extra></extra>"
    )
    fig_sunburst.update_layout(
        height=250, margin=dict(t=5, l=5, r=5, b=5),
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)'
    )
    sunburst_html = fig_sunburst.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False})

    context = {
        'section': section,
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
    }
    return render(request, 'dashboard/hod_dashboard.html', context)

# --- LOGIK MODUL KLINIKAL (CTS) LENGKAP ---
@login_required(login_url='/login/')
def cts_view(request):
    sub_module = request.GET.get('sub_module', 'Clinical')
    active_tab = request.GET.get('tab', 'Skills')
    dept = request.GET.get('dept', 'CTS')

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

    # --- 3. TAPIS DOKTOR & KIRA STATISTIK ---
    senarai_doktor = []
    semua_markah = [] 
    
    if pilihan_klinik == "All Clinics":
        for k in klinik_tersedia:
            df_docs = generate_mock_doctors(k['name'])
            senarai_doktor.extend(df_docs['Officer Name'].tolist())
            semua_markah.extend(df_docs['Current Score'].tolist()) # Tarik markah untuk kira purata
    else:
        df_docs = generate_mock_doctors(pilihan_klinik)
        senarai_doktor.extend(df_docs['Officer Name'].tolist())
        semua_markah.extend(df_docs['Current Score'].tolist())

    # Buang nama pendua dan susun
    senarai_doktor = list(set(senarai_doktor))
    senarai_doktor.sort()

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
                box_bg, box_border, box_text = '#fff1f2', '#be123c', '#881337'
                base_drop = np.random.uniform(0.6, 0.9) 
                fms_p_value = "< 0.001"
            elif doc_skill >= 33:
                prestasi = 'Moderate'
                box_bg, box_border, box_text = '#fff1f2', '#be123c', '#881337'
                base_drop = np.random.uniform(0.2, 0.45) 
                fms_p_value = f"0.0{np.random.randint(11, 49)}" 
            else:
                prestasi = 'Needs Improvement'
                box_bg, box_border, box_text = '#ffe4e6', '#be123c', '#881337' 
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
                line=dict(color='#881337', width=3), marker=dict(size=8, color='#be123c'),
                name='Achievement Probability'
            ))
            fig_km.update_layout(
                title=dict(text="<b>Kaplan-Meier Survival Analysis</b><br><span style='font-size:11px; color:#f43f5e;'>Probability of Achieving Target HbA1c < 7.0% Over Time</span>", font=dict(size=13, color="#881337")),
                xaxis_title="Monitoring Time (Months)", yaxis_title="Probability",
                yaxis=dict(tickformat='.0%', range=[0, 1.05], gridcolor='#ffe4e6'), xaxis=dict(gridcolor='#ffe4e6'), height=300,
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
            line_color = '#881337' if prestasi == 'Excellent' else ('#be123c' if prestasi == 'Moderate' else '#fb7185')
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
            
            fig_gee.add_hline(y=7.0, line_dash="solid", line_color="#be123c", line_width=1.5, annotation_text="Target (<7.0%)")
            
            fig_gee.update_layout(
                title=dict(text="<b>Longitudinal Trend (GEE Model)</b><br><span style='font-size:11px; color:#f43f5e;'>Individual Trajectories (Spaghetti) overlayed with Population-Averaged Mean</span>", font=dict(size=13, color="#881337")),
                xaxis_title="Visit (Months)", yaxis_title="Mean HbA1c (%)",
                height=300, margin=dict(t=50, l=40, r=20, b=40),
                paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                yaxis=dict(range=[5.0, 12.0], gridcolor='#ffe4e6'), xaxis=dict(gridcolor='#ffe4e6'),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(size=10, color='#881337'))
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
                name='CUSUM', line=dict(color='#881337', width=3), marker=dict(size=8, color='#be123c')
            ))
            
            fig.add_hline(y=0, line_dash="dash", line_color="#fecdd3", annotation_text="Baseline KPI (80)")
            fig.update_layout(
                title=dict(text=f"<b>Analisis CUSUM: {pilihan_doktor}</b>", font=dict(size=13, color="#881337")),
                margin=dict(t=35, l=0, r=0, b=0), 
                height=250, 
                paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                xaxis_title="Number of LIMA-LAD Procedures", yaxis_title="CUSUM Variation",
                xaxis=dict(tickmode='linear', tick0=1, dtick=1, gridcolor='#ffe4e6'), yaxis=dict(gridcolor='#ffe4e6')
            )
            
            chart_html = fig.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False})

    # ==========================================
    # TAB 2: KNOWLEDGE (GAUGES & TABLE)
    # ==========================================
    elif active_tab == 'Knowledge':
        import random
        
        # 1. Jana markah unik untuk setiap doktor (berubah ikut nama)
        random.seed(pilihan_doktor)
        cpd_score = random.randint(60, 98)
        
        # 2. Peer average ambil dari purata sebenar klinik (avg_performance)
        peer_score = avg_performance if avg_performance > 0 else 75

        # 3. Fungsi untuk tentukan teks dan status secara automatik (Clean Text)
        def get_status_info(score):
            if score >= 85: return "Outstanding", "outstanding"
            elif score >= 75: return "Good", "good"
            elif score >= 65: return "Competent", "competent"
            else: return "Needs Attention", "needs-attention"

        cpd_text, cpd_css_class = get_status_info(cpd_score)
        peer_text, peer_css_class = get_status_info(peer_score)

        # GAUGE DOKTOR INDIVIDU (TEMA ROSE)
        fig_ind = go.Figure(go.Indicator(
            mode = "gauge+number", value = cpd_score,
            title = {'text': "Your Clinical Score (%)", 'font': {'size': 16, 'color': '#881337'}},
            number = {'font': {'color': '#881337'}},
            gauge = {
                'axis': {'range': [0, 100], 'tickfont': {'color': '#881337'}}, 
                'bar': {'color': "#881337", 'thickness': 0.15},
                'steps': [{'range': [0, 65], 'color': "#fecdd3"}, {'range': [65, 75], 'color': "#fb7185"}, {'range': [75, 85], 'color': "#f43f5e"}, {'range': [85, 100], 'color': "#be123c"}]
            }
        ))
        fig_ind.update_layout(height=260, margin=dict(t=40, b=10, l=30, r=30), paper_bgcolor='rgba(0,0,0,0)')
        gauge_score_html = fig_ind.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False})

        # GAUGE PURATA RAKAN SEKERJA (PEER TEMA ROSE)
        fig_peer = go.Figure(go.Indicator(
            mode = "gauge+number", value = peer_score,
            title = {'text': "Peer Average (%)", 'font': {'size': 16, 'color': '#881337'}},
            number = {'font': {'color': '#881337'}},
            gauge = {
                'axis': {'range': [0, 100], 'tickfont': {'color': '#881337'}}, 
                'bar': {'color': "#be123c", 'thickness': 0.15},
                'steps': [{'range': [0, 65], 'color': "rgba(254, 205, 211, 0.5)"}, {'range': [65, 75], 'color': "rgba(251, 113, 133, 0.5)"}, {'range': [75, 85], 'color': "rgba(244, 63, 94, 0.5)"}, {'range': [85, 100], 'color': "rgba(190, 18, 60, 0.5)"}]
            }
        ))
        fig_peer.update_layout(height=260, margin=dict(t=40, b=10, l=30, r=30), paper_bgcolor='rgba(0,0,0,0)')
        gauge_peer_html = fig_peer.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False})

        # JADUAL SEJARAH (Topik dinamik mengikut FMS / CTS)
        topik_2 = "Diabetes Management" if dept == "FMS" else "LIMA-LAD Technique"
        history_df = pd.DataFrame({
            "Date": ["20/02/2026", "12/02/2026", "05/01/2026"], 
            "Category": ["CME", "Workshop", "Online Quiz"],
            "Topic": ["Trauma Management", topik_2, "Medical Ethics"], 
            "Marks": [min(100, cpd_score+2), min(100, cpd_score+5), max(0, cpd_score-4)]
        })
        history_html = history_df.to_html(index=False, classes="custom-table", escape=False)

    # ==========================================
    # TAB 3: DOCUMENTATION (BAR & TABLE)
    # ==========================================
    elif active_tab == 'Documentation':
        # Guna PURE Graph Objects untuk Bar Mendatar Bertindan (Stacked Horizontal Bar)
        fig_time = go.Figure()
        
        fig_time.add_trace(go.Bar(
            y=["Patient Records"], x=[85], name="On-time", orientation='h',
            marker=dict(color="#881337"), text="85%", textposition="inside", insidetextanchor="middle"
        ))
        fig_time.add_trace(go.Bar(
            y=["Patient Records"], x=[10], name="Delayed", orientation='h',
            marker=dict(color="#f43f5e"), text="10%", textposition="inside", insidetextanchor="middle"
        ))
        fig_time.add_trace(go.Bar(
            y=["Patient Records"], x=[5], name="Overdue", orientation='h',
            marker=dict(color="#fecdd3"), text="5%", textposition="inside", insidetextanchor="middle"
        ))
        
        fig_time.update_layout(
            barmode='stack', 
            height=150, 
            margin=dict(l=0, r=0, t=10, b=0),
            paper_bgcolor='rgba(0,0,0,0)', 
            plot_bgcolor='rgba(0,0,0,0)',
            xaxis=dict(range=[0, 100], showgrid=False),
            yaxis=dict(showgrid=False),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        time_chart_html = fig_time.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False})

        # Jadual kekal sama
        df_delayed = pd.DataFrame({
            "Doctor": ["Dr. Ahmad", "Dr. Siti Fazura", "Dr. Chong Wei"],
            "Count": [3, 2, 1],
            "Delay (Avg)": ["5.5 Hours", "4.5 Hours", "8.0 Hours"],
            "Reason": ["Shortage of staff in ward", "Red Zone emergency case", "System login delayed"]
        })
        delayed_html = df_delayed.to_html(index=False, classes="custom-table", escape=False)

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
# ==========================================
# MODUL OPERASIONAL
# ==========================================
@login_required(login_url='/login/')
def operational_view(request):
    sub_module = request.GET.get('sub_module', 'Operational')
    active_tab = request.GET.get('tab', 'Productivity')
    dept = request.GET.get('dept', 'CTS')
    
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

    # --- 3. TAPIS DOKTOR & KIRA STATISTIK ---
    senarai_doktor = []
    semua_markah = [] 
    
    if pilihan_klinik == "All Clinics":
        for k in klinik_tersedia:
            df_docs = generate_mock_doctors(k['name'])
            senarai_doktor.extend(df_docs['Officer Name'].tolist())
            semua_markah.extend(df_docs['Current Score'].tolist())
    else:
        df_docs = generate_mock_doctors(pilihan_klinik)
        senarai_doktor.extend(df_docs['Officer Name'].tolist())
        semua_markah.extend(df_docs['Current Score'].tolist())

    senarai_doktor = list(set(senarai_doktor))
    senarai_doktor.sort()

    pilihan_doktor = request.GET.get('officer')
    if not pilihan_doktor or pilihan_doktor not in senarai_doktor:
        pilihan_doktor = senarai_doktor[0] if senarai_doktor else "Tiada Pegawai"

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
        
        # Dapatkan User sebenar berdasarkan pilihan_doktor
        doc_username_clean = pilihan_doktor.split(" (")[0].strip() if " (" in pilihan_doktor else pilihan_doktor.strip()
        target_user = User.objects.filter(username__icontains=doc_username_clean).first()
        if not target_user:
            target_user = User.objects.filter(first_name__icontains=doc_username_clean).first()

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
            line=dict(color='#be123c', width=3, shape='spline', smoothing=1.0),
            marker=dict(size=8, color='#881337', line=dict(color='white', width=2)),
            fillcolor='rgba(190, 18, 60, 0.15)',
            name='Patients Cleared'
        ))
        
        fig_area.update_layout(
            height=280, 
            margin=dict(l=0, r=0, t=10, b=0), 
            paper_bgcolor='rgba(0,0,0,0)', 
            plot_bgcolor='rgba(0,0,0,0)',
            font=dict(color="#881337"),
            yaxis=dict(range=[0, 80], gridcolor='#ffe4e6'), 
            xaxis_title="Date",               
            yaxis_title="Patients Cleared",   
            xaxis=dict(showgrid=False)
        )
        area_chart_html = fig_area.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False})
    # 2. Tab Innovation (PILIHAN 3: LOLLIPOP CHART)
    elif active_tab == 'Innovation':
        kategori = ['System Compliance', 'Proactive Thinking', 'Team Delegation', 'Crisis Response']
        
        random.seed(pilihan_doktor + "lollipop")
        markah = [random.randint(65, 95) for _ in range(4)]

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
            marker=dict(color='#be123c', size=16, line=dict(color='white', width=2)),
            text=markah,
            textposition='middle right',
            textfont=dict(color='#881337', size=13, weight='bold'),
            hoverinfo='none'
        ))

        fig_pop.update_layout(
            height=320, margin=dict(t=30, b=30, l=10, r=40),
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            font=dict(color="#881337"),
            showlegend=False,
            xaxis=dict(range=[0, 105], showgrid=True, gridcolor='#ffe4e6', title=dict(text='Score (%)', font=dict(color='#881337'))),
            yaxis=dict(showgrid=False, tickfont=dict(size=12, color='#881337', weight='bold'))
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
    dept = request.GET.get('dept', 'CTS')
    
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

    # --- 3. TAPIS DOKTOR & KIRA STATISTIK ---
    senarai_doktor = []
    semua_markah = [] 
    
    if pilihan_klinik == "All Clinics":
        for k in klinik_tersedia:
            df_docs = generate_mock_doctors(k['name'])
            senarai_doktor.extend(df_docs['Officer Name'].tolist())
            semua_markah.extend(df_docs['Current Score'].tolist()) # Tarik markah untuk kira purata
    else:
        df_docs = generate_mock_doctors(pilihan_klinik)
        senarai_doktor.extend(df_docs['Officer Name'].tolist())
        semua_markah.extend(df_docs['Current Score'].tolist())

    senarai_doktor = list(set(senarai_doktor))
    senarai_doktor.sort()

    pilihan_doktor = request.GET.get('officer')
    if not pilihan_doktor or pilihan_doktor not in senarai_doktor:
        pilihan_doktor = senarai_doktor[0] if senarai_doktor else "Tiada Pegawai"

    # ==========================================
    # LOGIK INTEGRASI MyPSQ HOD (FASA 4)
    # ==========================================
    # Dapatkan objek User berdasarkan username atau nama doktor
    doc_username_clean = pilihan_doktor.split(" (")[0].strip() if " (" in pilihan_doktor else pilihan_doktor.strip()
    target_user = User.objects.filter(username__icontains=doc_username_clean).first()
    if not target_user:
        target_user = User.objects.filter(first_name__icontains=doc_username_clean).first()

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
    else:
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
    
    random.seed(pilihan_doktor + "softskills")
    markah_radar = [random.randint(65, 95) for _ in range(5)]
    
    # 5 tona warna pelbagai untuk nampak menarik (TEMA ROSE KORPORAT)
    warna_rose = ['#881337', '#be123c', '#e11d48', '#fb7185', '#fecdd3']

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
    if active_tab == 'EI':
        # Gunakan nama doktor sebagai seed supaya graf unik untuk setiap staf
        random.seed(pilihan_doktor + "EI_Skills") 
        
        ei_comp = ["Self-Awareness", "Emotional Regulation", "Social Skills", "Empathy"]
        ei_scores = [random.randint(65, 95) for _ in range(4)]
        
        # Berikan 4 warna berbeza (Tona Rose MyPrestasi)
        warna_palang = ['#fecdd3', '#fda4af', '#e11d48', '#881337']
        
        fig_ei = go.Figure(go.Bar(
            x=ei_scores, y=ei_comp, orientation='h', 
            marker_color=warna_palang, # Gunakan senarai warna tadi
            text=ei_scores, textposition='auto',
            textfont=dict(color='white', weight='bold') # Putihkan teks supaya timbul
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
            np.random.seed(len(pilihan_doktor) + 20)
            roles = ["Peers", "Support Staff", "Supervisors"]
            fb_scores = [np.random.randint(80, 98) for _ in range(3)]
            
            fig_team = go.Figure(go.Bar(
                x=roles, y=fb_scores, marker_color='#be123c', text=fb_scores, textposition='auto'
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
            color_map = {"General Clinical": "#881337", "Procedural": "#e11d48", "Consultation": "#fb7185"}
            
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
# MODUL STAFF (MEDICAL OFFICER PORTAL)
# ==========================================
from django.contrib import messages
from datetime import date
import random # <--- PASTIKAN IMPORT INI ADA

# ==========================================
# MODUL STAFF (MEDICAL OFFICER PORTAL)
# ==========================================
# 1. PADAM ATAU HIDE BARIS INI:
# @login_required(login_url='/login/')

def staff_dashboard_view(request):
    
    # 2. TAMBAH SEMAKAN MANUAL INI: Jika bukan staff, tendang ke login
    if request.session.get('role') != 'staff':
        return redirect('login')

    section = request.GET.get('section', 'Dashboard')
    
    if request.method == 'POST':
        form_type = request.POST.get('form_type')
    
    if request.method == 'POST':
        form_type = request.POST.get('form_type')
        
        # --- 1. MODUL KLINIKAL ---
        if form_type in ['skills', 'skills_cts', 'skills_fms']:
            proc_name = request.POST.get('procedure_name', 'Clinical Case Encounter')
            score_val = float(request.POST.get('score', 85.0))
            patient_rn = request.POST.get('patient_id') or request.POST.get('mrn') or f'RN-{random.randint(10000, 99999)}'
            
            if request.user.is_authenticated:
                ProcedureLog.objects.create(officer=request.user, procedure_name=proc_name, score=score_val)
                DocumentationLog.objects.create(officer=request.user, patient_id=patient_rn, delay_hours=0.0, is_ontime=True)
            messages.success(request, f"✅ Clinical record for {patient_rn} saved & auto-synced to Documentation Log!")

        elif form_type == 'cpd':
            mata = int(request.POST.get('mata', '10'))
            tajuk = request.POST.get('tajuk', 'Program CME')
            category = request.POST.get('category', 'CME Workshop')
            if request.user.is_authenticated:
                CPDClaim.objects.create(officer=request.user, topic=tajuk, points=mata, category=category, event_date=date.today())
            messages.success(request, f"✅ Claim of {mata} CPD points for '{tajuk}' saved to DB.")

        elif form_type == 'doc':
            patient_id = request.POST.get('patient_id', f'RN-{random.randint(10000, 99999)}')
            delay_hrs = float(request.POST.get('delay_hours', 0.0))
            reason = request.POST.get('reason', '')
            if request.user.is_authenticated:
                DocumentationLog.objects.create(officer=request.user, patient_id=patient_id, delay_hours=delay_hrs, reason_for_delay=reason, is_ontime=(delay_hrs == 0))
            messages.success(request, f"✅ Documentation record for patient {patient_id} recorded in DB.")

        elif form_type == 'policy':
            if request.user.is_authenticated:
                PolicyDeclaration.objects.create(
                    officer=request.user,
                    score_domain_a=float(request.POST.get('score_a', 85.0)),
                    score_domain_b=float(request.POST.get('score_b', 90.0)),
                    score_domain_c=float(request.POST.get('score_c', 70.0)),
                    score_domain_d=float(request.POST.get('score_d', 92.0)),
                    score_domain_e=float(request.POST.get('score_e', 88.0)),
                )
            messages.success(request, "✅ Compliance declaration successfully recorded in DB.")

        # --- 2. MODUL OPERASIONAL ---
        elif form_type == 'ops_prod':
            # Mengambil data dari HTML _operational.html (name="outpatient_count", dll)
            outpatient_count = int(request.POST.get('outpatient_count', 30))
            avg_consult = float(request.POST.get('avg_consult', 15.0))
            punctuality = request.POST.get('punctuality', 'On-Time')
            p_rate = 100.0 if 'On-Time' in punctuality else 80.0

            if request.user.is_authenticated:
                ProductivityLog.objects.create(
                    officer=request.user,
                    patients_cleared=outpatient_count,
                    avg_consult_minutes=avg_consult,
                    punctuality_rate=p_rate
                )
            messages.success(request, "✅ Productivity data successfully recorded into Database.")

        elif form_type == 'ops_innov':
            # Form inovasi / research
            title = request.POST.get('title', 'System Improvement / Research')
            if request.user.is_authenticated:
                InnovationProject.objects.create(
                    officer=request.user,
                    project_title=title,
                    category="Innovation & Research",
                    description="Submitted via MyPrestasi Portal",
                    score=85.0
                )
            messages.success(request, "✅ Your innovation/research record has been saved to DB.")

        # --- 3. MODUL INTERPERSONAL ---
        elif form_type == 'int_ei':
            notes = request.POST.get('ei_reflection', 'Self debrief completed.')
            if request.user.is_authenticated:
                EmotionalReflection.objects.create(
                    officer=request.user,
                    self_awareness_score=85,
                    reflection_notes=notes
                )
            messages.success(request, "✅ Emotional intelligence reflection log successfully recorded in DB.")

        elif form_type == 'int_team':
            peer_name = request.POST.get('peer_name', 'Unknown Peer')
            points_awarded = int(request.POST.get('kudos_category', 5))
            message = request.POST.get('kudos_note', 'Good job!')
            
            if request.user.is_authenticated:
                # Cari user penerima (fallback kepada user semasa jika tak jumpa)
                receiver_user = User.objects.filter(username__icontains=peer_name.split(' ')[-1]).first() or request.user
                PeerKudos.objects.create(
                    sender=request.user,
                    receiver=receiver_user,
                    category="Peer Endorsement",
                    points=points_awarded,
                    message=message
                )
            messages.success(request, f"✅ {points_awarded} Kudos points awarded to {peer_name}.")

        elif form_type == 'int_mentor':
            mentee = request.POST.get('mentee_name', 'Junior MO')
            topic = request.POST.get('mentor_domain', 'Clinical Guidance')
            date_val = request.POST.get('mentor_date', date.today())
            
            if request.user.is_authenticated:
                MentorshipSession.objects.create(
                    mentor=request.user,
                    mentee_name=mentee,
                    topic=topic,
                    session_date=date_val,
                    duration_hours=1.0
                )
            messages.success(request, "✅ Mentorship session securely saved in DB.")

        # --- SETTINGS & COACHING ---
        elif form_type == 'settings':
            messages.success(request, "✅ Profile & Account Settings successfully updated.")
        elif form_type == 'complete_coaching':
            request.session['coaching_completed'] = True
            messages.success(request, "⚡ AI Self-Coaching plan acknowledged! Your Operational Index has been upgraded to 88% (Target Achieved).")
            
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
            marker=dict(colors=["#881337", "#be123c", "#fecdd3"]), textinfo='none' # Tema Rose
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
            line=dict(color='#be123c', width=4, shape='spline'),
            marker=dict(size=8, color='#881337'),
            fillcolor='rgba(190, 18, 60, 0.2)', # Tema Rose
            name='Tasks'
        ))

        fig_area.update_layout(
            height=220, margin=dict(t=10, b=0, l=0, r=10), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', 
            xaxis_title="", yaxis_title="Tasks Completed", 
            xaxis=dict(showgrid=False), yaxis=dict(showgrid=True, gridcolor='#ffe4e6')
        )
        area_html = fig_area.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False})

        # 3. GAUGE CHART (Performance Index - MyPrestasi Rose & Burgundy DNA)
        fig_gauge = go.Figure(go.Indicator(
            mode="gauge+number", value=4.2, number={'font':{'size':36, 'color':'#881337', 'family':'Inter'}},
            gauge={
                'axis': {'range': [0, 5], 'tickwidth': 1, 'tickcolor': "#881337", 'tickfont': {'color': '#881337', 'family': 'Inter', 'size': 11}}, 
                'bar': {'color': "#881337", 'thickness': 0.22},
                'bgcolor': "white",
                'borderwidth': 1,
                'bordercolor': "#ffe4e6",
                'steps': [
                    {'range': [0, 3], 'color': "#fff1f2"}, 
                    {'range': [3, 4], 'color': "#ffe4e6"}, 
                    {'range': [4, 5], 'color': "#fecdd3"}
                ],
                'threshold': {'line': {'color': "#be123c", 'width': 5}, 'thickness': 0.8, 'value': 4.5}
            }
        ))
        fig_gauge.update_layout(height=220, margin=dict(t=30, b=10, l=20, r=20), paper_bgcolor='rgba(0,0,0,0)')
        gauge_html = fig_gauge.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False})

    # --- PERFORMANCE TAB (Holistic Benchmark 10 Modul - SPIDER WEB) ---
    elif section == 'Performance':
        categories = ['Procedure Log', 'CPD Points', 'Doc. Audit', 'Policies', 'Productivity', 'Innovation', 'Patient Exp.', 'Emotional Intel.', 'Teamwork', 'Mentorship']
        scores = [85, 90, 75, 95, 92, 60, 88, 82, 90, 70]
        
        kategori_penuh = categories + [categories[0]]
        markah_penuh = scores + [scores[0]]

        fig_radar = go.Figure()
        fig_radar.add_trace(go.Scatterpolar(
            r=markah_penuh, theta=kategori_penuh, fill='toself',
            fillcolor='rgba(190, 18, 60, 0.25)', # Warna Merah Rose Lutsinar
            line=dict(color='#881337', width=3), # Garisan Burgundy
            marker=dict(size=8, color='#be123c', symbol='circle'),
            hoverinfo='r+theta', name='My Score'
        ))
        
        fig_radar.update_layout(
            polar=dict(
                bgcolor='rgba(255, 241, 242, 0.5)', # Latar belakang bulatan Rose cair
                radialaxis=dict(visible=True, range=[0, 100], showgrid=True, gridcolor='#fecdd3', tickfont=dict(size=10, color='#881337'), angle=90),
                angularaxis=dict(showgrid=True, gridcolor='#fecdd3', linecolor='#fecdd3', tickfont=dict(size=11, color='#881337', weight='bold'))
            ), 
            showlegend=False, height=380, margin=dict(t=40, b=40, l=60, r=60), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)'
        )
        radar_html = fig_radar.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False})

    # DUA BARIS INI TELAH DIKELUARKAN DARI BLOK ELIF (DITOLAK KE KIRI)
    staff_name = request.session.get('staff_name', 'Dr. Portal')
    staff_dept = request.session.get('staff_dept', 'Outpatient')

    # ==========================================
    # INTEGRASI MyPSQ REAL-TIME (FASA 3)
    # ==========================================
    staff_user = request.user if request.user.is_authenticated else User.objects.filter(profile__role='STAFF').first()
    if not staff_user:
        staff_user = User.objects.first()

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
    # LOGIK DIAGNOSTIK AI EVALUATOR (3 TUNJANG)
    # ==========================================
    random.seed(staff_name)
    
    score_clinical = random.randint(75, 95)
    score_interpersonal = random.randint(80, 98)
    
    # Check if MO has completed the self-coaching module
    if request.session.get('coaching_completed'):
        score_ops_coding = 88  
        ai_risk_tier = "GREEN"
        ai_alert_title = "AI Diagnostic Alert: Remediation Completed"
        ai_alert_msg = f"Congratulations! Clinical Coding tasks ({score_ops_coding}%) have been successfully completed. All 3 Pillars are now in optimal range."
        ai_action_text = "View Verified Status"
    else:
        score_ops_coding = random.randint(40, 48)  
        
        # Determine Risk Tier based on 3-Pillar score ranges
        if score_ops_coding < 50 or score_clinical < 65:
            ai_risk_tier = "RED"
            ai_alert_title = "AI Diagnostic Alert: Operational Lag Detected"
            ai_alert_msg = f"AI Engine detected an efficiency drop in Task Velocity (Clinical Coding: {score_ops_coding}%) and documentation time backlog. Your Clinical ({score_clinical}%) & Interpersonal ({score_interpersonal}%) performance remain outstanding."
            ai_action_text = "Start AI Self-Coaching"
        elif score_ops_coding < 75 or score_clinical < 80:
            ai_risk_tier = "YELLOW"
            ai_alert_title = "AI Diagnostic Alert: Moderate Attention Needed"
            ai_alert_msg = f"Your overall performance is stable, but documentation completion rate is at a borderline level ({score_ops_coding}%). Self-review is recommended."
            ai_action_text = "Review Guidance"
        else:
            ai_risk_tier = "GREEN"
            ai_alert_title = "AI Diagnostic Alert: Outstanding Performance"
            ai_alert_msg = f"Congratulations! All 3 Pillars are at an outstanding level (>85%). You meet the eligibility criteria for the Excellent Service Award."
            ai_action_text = "View Talent Track"

    # ==========================================
    # PROMOTION READINESS INDEX (PRI) CALCULATION (LIVE DB INTEGRATION)
    # ==========================================
    if request.user.is_authenticated:
        # Panggil Enjin Pengira Skor Bersepadu untuk Staf ini (Menggunakan .get untuk elak KeyError)
        scores = calculate_pri_score(request.user)
        score_clinical = scores.get('score_clinical', 85)
        score_ops_coding = scores.get('score_operational', 80)
        score_interpersonal = scores.get('score_interpersonal', 88)
        score_cpd = scores.get('score_cpd', 90)
        pri_score = scores.get('pri_score', 85)
        promotion_tier = scores.get('promotion_tier', 'Tier 1: Fast-Track Candidate')
        promotion_color = scores.get('promotion_color', '#16a34a')
        next_grade = scores.get('next_grade', 'UD48 (Fast-Track)')
    else:
        # Fallback simulasi jika log masuk tanpa auth
        score_cpd = 90
        pri_score = int((0.40 * score_clinical) + (0.30 * score_ops_coding) + (0.20 * score_interpersonal) + (0.10 * score_cpd))
        if pri_score >= 85:
            promotion_tier = "Tier 1: Fast-Track Candidate"
            promotion_color = "#16a34a"
            next_grade = "UD48 (Fast-Track)"
        elif pri_score >= 70:
            promotion_tier = "Tier 2: On-Track (Time-Based)"
            promotion_color = "#d97706"
            next_grade = "UD48 (Time-Based)"
        else:
            promotion_tier = "Tier 3: Action Required"
            promotion_color = "#be123c"
            next_grade = "UD48 (Deferred)"

    # Tarik rekod DocumentationLog & ProductivityLog sebenar dari DB bagi staf ini
    doc_logs = DocumentationLog.objects.filter(officer=staff_user).order_by('-created_at') if staff_user else []
    prod_logs = ProductivityLog.objects.filter(officer=staff_user).order_by('-id') if staff_user else []

    # Kira metrik ringkasan harian secara automatik dari data DB
    if prod_logs.exists():
        prod_cleared_avg = int(sum([p.patients_cleared for p in prod_logs]) / prod_logs.count())
        prod_consult_avg = round(sum([p.avg_consult_minutes for p in prod_logs]) / prod_logs.count(), 1)
        prod_punctuality_avg = round(sum([p.punctuality_rate for p in prod_logs]) / prod_logs.count(), 0)
    else:
        prod_cleared_avg = 42
        prod_consult_avg = 12.0
        prod_punctuality_avg = 95.0

    context = {
        'section': section,
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
    Paparan Pusat Kawalan Khas untuk Admin HPU / Developer
    """
    if request.session.get('role') != 'admin_hpu':
        return redirect('login')

    # LOGIK MENDAFTAR PENGGUNA BAHARU
    if request.method == 'POST' and request.POST.get('form_type') == 'create_user':
        username = request.POST.get('username').strip()
        password = request.POST.get('password').strip()
        role = request.POST.get('role')
        department = request.POST.get('department')

        # Semak jika username sudah wujud
        if User.objects.filter(username=username).exists():
            messages.error(request, f"Ralat: ID Pengguna '{username}' sudah wujud di dalam sistem!")
        else:
            # Cipta User & Profil serentak
            new_user = User.objects.create_user(username=username, password=password)
            UserProfile.objects.create(user=new_user, role=role, department=department)
            messages.success(request, f"✅ Akaun '{username}' ({role} - {department}) berjaya didaftarkan!")
            return redirect('hpu_dashboard')

    # Dapatkan senarai semua profil untuk dipaparkan di dalam jadual
    all_users = UserProfile.objects.all().select_related('user').order_axis('-id') if hasattr(UserProfile.objects, 'order_axis') else UserProfile.objects.all().select_related('user').order_by('-id')

    context = {
        'users': all_users
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
# DEDICATED OFFICER COGNITIVE PROFILE
# ==========================================
@login_required(login_url='/login/')
def officer_profile_view(request):
    officer_name = request.GET.get('officer', 'Pegawai Perubatan')
    clinic_name = request.GET.get('clinic', 'Klinik Kesihatan')
    state_name = request.GET.get('state', 'State')
    
    # Jana data mentah berasaskan nama (Supaya sentiasa konsisten bila di-refresh)
    import random
    random.seed(officer_name)
    
    score_clinical = random.randint(75, 98)
    score_ops = random.randint(70, 95)
    score_interp = random.randint(65, 95)
    score_cpd = random.randint(80, 100)
    score_doc = random.randint(85, 100)
    total_score = int((score_clinical + score_ops + score_interp) / 3)
    
    # Status Penilaian
    status = "Outstanding" if total_score >= 85 else ("Competent" if total_score >= 65 else "Needs Attention")
    
    # Radar Chart Kognitif
    categories = ['Clinical Skill', 'CPD Knowledge', 'Doc Quality', 'Emotional Intel', 'Teamwork']
    values = [score_clinical, score_cpd, score_doc, score_interp, random.randint(70, 95)]
    
    fig_radar = go.Figure()
    fig_radar.add_trace(go.Scatterpolar(
        r=values + [values[0]],
        theta=categories + [categories[0]],
        fill='toself',
        fillcolor='rgba(190, 18, 60, 0.2)',
        line=dict(color='#be123c', width=2),
        marker=dict(size=6, color='#881337')
    ))
    fig_radar.update_layout(
        polar=dict(
            radialaxis=dict(visible=True, range=[0, 100], tickfont=dict(size=9, color='#881337')),
            angularaxis=dict(tickfont=dict(size=10, color='#881337', weight='bold'))
        ),
        margin=dict(t=20, b=20, l=30, r=30),
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
        showlegend=False, height=320
    )
    radar_html = fig_radar.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False})

    context = {
        'officer_name': officer_name,
        'clinic_name': clinic_name,
        'state_name': state_name,
        'total_score': total_score,
        'status': status,
        'score_clinical': score_clinical,
        'score_ops': score_ops,
        'score_interp': score_interp,
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
        {'type': 'Fast-Track Candidates (PRI ≥ 85%)', 'officer': '24 Medical Officers', 'facility': 'Multiple Facilities', 'severity': 'Success', 'time': 'Today, 07:00 AM', 'detail': 'AI shortlisted 24 Officers eligible for Fast-Track UD48 promotion.'},
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
        line=dict(color="#f43f5e", width=2, dash='dash')
    ))
    fig_trend.add_trace(go.Scatter(
        x=months, y=actual_trend, mode='lines+markers', name="Actual Performance",
        line=dict(color="#881337", width=3), marker=dict(size=8, color="#be123c"),
        fill='tozeroy', fillcolor='rgba(190, 18, 60, 0.08)'
    ))
    fig_trend.update_layout(
        height=320, margin=dict(l=30, r=20, t=20, b=30),
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(size=11, color='#881337', family='Inter')),
        xaxis=dict(showgrid=False, tickfont=dict(size=11, color='#881337', weight='bold', family='Inter')),
        yaxis=dict(showgrid=True, gridcolor='#ffe4e6', range=[60, 100], tickfont=dict(size=10, color='#881337', family='Inter'))
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
        colorscale=[[0, '#fff1f2'], [0.4, '#fecdd3'], [0.7, '#f43f5e'], [1.0, '#be123c']],
        texttemplate="<b>%{z}%</b>", textfont=dict(size=11, color="#ffffff", family='Inter'),
        showscale=False, xgap=5, ygap=5,
        hovertemplate="<b>Pegawai:</b> %{y}<br><b>Tunjang:</b> %{x}<br><b>Skor:</b> %{z}%<extra></extra>"
    ))
    fig_heat.update_layout(
        height=380, margin=dict(l=120, r=20, t=20, b=30),
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
        xaxis=dict(fixedrange=True, tickfont=dict(size=11, color='#881337', weight='bold', family='Inter')),
        yaxis=dict(fixedrange=True, tickfont=dict(size=10, color='#881337', weight='bold', family='Inter'), autorange="reversed")
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
    Dedicated Insight Page: Medical Officers Directory & Roster Allocation
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
    # Cuba cari User di DB, jika tiada guna nama paparan fallback (supaya ujian tidak 404)
    try:
        doctor_obj = User.objects.get(username=doctor_username)
        doctor_profile = getattr(doctor_obj, 'profile', None)
        doc_full_name = f"{doctor_obj.first_name} {doctor_obj.last_name}".strip() or doctor_username
        facility_name = getattr(doctor_profile, 'assigned_facility', 'Klinik Kesihatan Medong') if doctor_profile else 'Klinik Kesihatan Medong'
    except User.DoesNotExist:
        doctor_obj = User.objects.first() # Guna akaun pertama jika ada
        doc_full_name = urllib.parse.unquote(doctor_username).replace('_', ' ').title()
        facility_name = 'Klinik Kesihatan Medong'

    # Jika pesakit telah menekan butang Submit
    if request.method == 'POST':
        q1 = int(request.POST.get('q1', 5))
        q2 = int(request.POST.get('q2', 5))
        q3 = int(request.POST.get('q3', 5))
        q4 = int(request.POST.get('q4', 5))
        q5 = int(request.POST.get('q5', 5))
        q6 = int(request.POST.get('q6', 5))
        q7 = int(request.POST.get('q7', 5))
        
        comments = request.POST.get('patient_comments', '')
        pt_category = request.POST.get('patient_category', 'Outpatient')

        # Jika doctor_obj wujud, simpan terus ke pangkalan data
        if doctor_obj:
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

    