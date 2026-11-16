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
from django.db.models import Avg, Q
from django.utils import timezone

# Import Data Simulasi (Mock Data)
from .mock_data import STATE_CENTERS, MOCK_KLINIK, MOCK_CUSUM_DATA

# Import Enjin Pengira Skor & Model Pangkalan Data MyPrestasi
from .services import (
    MODULE_ORDER,
    _kkm_hba1c_result,
    build_cpd_trajectory,
    calculate_cpd_points,
    calculate_pri_score,
    get_module_scores,
)
from .models import (
    UserProfile, MyPSQFeedback, ProcedureLog, PolicyDeclaration,
    PolicyItemResponse, DocumentationLog, CPDClaim, ProductivityLog,
    InnovationProject, EmotionalReflection, PeerKudos, MentorshipSession,
    HODFeedback,
    CusumSnapshot, CusumEpisode,  # CUSUM Shadow Mode Models
    KKM_CORE_DOMAINS, KKM_DOMAIN_CHOICES,
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


def _hod_scope_label(hod_scope, hod_state=None, hod_facility=None):
    """Label skop HOD sebenar tanpa nama fasiliti rekaan."""
    if hod_scope == 'FACILITY' and hod_facility:
        return hod_facility
    if hod_scope == 'STATE' and hod_state:
        return f'{hod_state} (State)'
    return 'All Facilities'


def _build_hod_staff_picker(department, state=None, clinic=None):
    """Bina picker HOD daripada staf sebenar sahaja (tiada mock doctor)."""
    from django.db.models import Q

    staff_qs = User.objects.filter(
        profile__role='STAFF',
        profile__department=department,
    )
    if state and state != 'All States':
        staff_qs = staff_qs.filter(
            Q(profile__assigned_state=state)
            | Q(profile__assigned_state__isnull=True)
            | Q(profile__assigned_state__exact='')
        )
    if clinic and clinic != 'All Clinics':
        staff_qs = staff_qs.filter(
            Q(profile__assigned_facility=clinic)
            | Q(profile__assigned_facility__isnull=True)
            | Q(profile__assigned_facility__exact='')
        )

    staff_qs = staff_qs.select_related('profile').order_by(
        'first_name', 'last_name', 'username'
    )
    labels = []
    staff_by_label = {}
    for staff in staff_qs:
        full_name = f"{staff.first_name} {staff.last_name}".strip()
        display_name = full_name or staff.username
        label = f"🟢 Dr. {display_name} (Live)"
        if label in staff_by_label:
            label = f"🟢 Dr. {display_name} [{staff.username}] (Live)"
        labels.append(label)
        staff_by_label[label] = staff

    return labels, staff_by_label


def _mean_score(values):
    """Purata nilai skor yang sah; None jika tiada nilai."""
    valid = [float(value) for value in values if value is not None]
    return round(sum(valid) / len(valid), 1) if valid else None


def _hod_pending_validation_count(staff_users):
    """Bilangan item belum disahkan untuk staf dalam skop HOD."""
    staff_ids = [staff.id for staff in staff_users]
    if not staff_ids:
        return 0
    return (
        ProcedureLog.objects.filter(
            officer_id__in=staff_ids, is_verified=False
        ).count()
        + CPDClaim.objects.filter(
            officer_id__in=staff_ids, status='PENDING'
        ).count()
        + PolicyDeclaration.objects.filter(
            officer_id__in=staff_ids, is_verified=False
        ).count()
    )


def _hod_cusum_alerts(staff_users):
    """Snapshot CUSUM dokumentasi yang masih mempunyai alert aktif."""
    return list(
        CusumSnapshot.objects.filter(
            module='documentation',
            officer__in=staff_users,
            alert_count__gt=0,
        ).select_related('officer')
    )


def _hod_tier_label(score):
    if score is None:
        return 'No Data'
    if score >= 85:
        return 'Tier 1'
    if score >= 70:
        return 'Tier 2'
    return 'Tier 3'


def _build_hod_module_scorecards(staff_users, labels_by_user_id=None):
    """Agregasi 10 modul untuk semua staf HOD.

    Clinical Delivery di-pool menggunakan numerator/denominator sebenar.
    Modul lain menggunakan purata skor scorable sahaja; NO_DATA tidak
    dianggap sebagai sifar. ``labels_by_user_id`` membolehkan picker dan
    ringkasan menggunakan label yang sama (termasuk disambiguasi username).
    """
    labels_by_user_id = labels_by_user_id or {}
    buckets = {
        key: {
            'key': key,
            'label': label,
            'scores': [],
            'numerator': 0.0,
            'denominator': 0,
            'has_records': False,
            'pending': False,
        }
        for key, label in MODULE_ORDER
    }
    summaries = []

    for staff in staff_users:
        result = get_module_scores(staff)
        module_scores = {
            module['key']: module['score'] for module in result['modules']
        }
        full_name = f"{staff.first_name} {staff.last_name}".strip()
        display_name = full_name or staff.username
        module_average = _mean_score(module_scores.values())
        clinical_score = _mean_score([
            module_scores.get('clinical_delivery'),
            module_scores.get('documentation'),
            module_scores.get('cpd'),
            module_scores.get('policies'),
        ])
        operational_score = _mean_score([
            module_scores.get('productivity'),
            module_scores.get('innovation'),
        ])
        interpersonal_score = _mean_score([
            module_scores.get('mypsq'),
            module_scores.get('emotional_intelligence'),
            module_scores.get('teamwork'),
            module_scores.get('mentorship'),
        ])
        summary = {
            'username': staff.username,
            'name': f"Dr. {display_name}",
            'label': (
                labels_by_user_id.get(staff.id)
                or f"🟢 Dr. {display_name} (Live)"
            ),
            'module_average': module_average,
            'tier': _hod_tier_label(module_average),
            'clinical_score': clinical_score,
            'operational_score': operational_score,
            'interpersonal_score': interpersonal_score,
            'module_scores': module_scores,
            'scorable_modules': sum(
                1 for value in module_scores.values() if value is not None
            ),
        }
        summaries.append(summary)

        for module in result['modules']:
            bucket = buckets.get(module['key'])
            if bucket is None:
                continue
            bucket['has_records'] = (
                bucket['has_records'] or module['has_records']
            )
            if module['status'] in ('PENDING_VERIFICATION', 'PROVISIONAL'):
                bucket['pending'] = True
            if module['key'] == 'clinical_delivery':
                bucket['denominator'] += int(module['denominator'] or 0)
                if isinstance(module['raw_value'], (int, float)):
                    bucket['numerator'] += float(module['raw_value'])
            if module['score'] is not None:
                bucket['scores'].append(float(module['score']))

    cards = []
    for key, label in MODULE_ORDER:
        bucket = buckets[key]
        if key == 'clinical_delivery' and bucket['denominator']:
            score = round(
                (bucket['numerator'] / bucket['denominator']) * 100.0,
                1,
            )
            n = bucket['denominator']
        else:
            score = _mean_score(bucket['scores'])
            n = len(bucket['scores'])

        if score is None:
            status = (
                'PENDING_VERIFICATION'
                if bucket['pending'] else 'NO_DATA'
            )
            display = 'No Data'
        else:
            status = 'ZERO' if score == 0 else 'OK'
            display = f'{score:.1f}%'

        cards.append({
            'key': key,
            'label': label,
            'score': score,
            'display': display,
            'status': status,
            'n': n,
            'has_records': bucket['has_records'],
            'is_pooled': key == 'clinical_delivery',
        })

    return cards, summaries


def _score_badge_html(score):
    """Lencana status berasaskan skor sebenar; tiada skor = No Data."""
    if score is None:
        return (
            '<span style="background:#f3edff;color:#6d3ec4;border:1px solid '
            '#e7dff2;padding:3px 8px;border-radius:12px;font-weight:800;'
            'font-size:10px;">No Data</span>'
        )
    if score >= 85:
        return (
            '<span style="background:#dcfce7;color:#15803d;border:1px solid '
            '#bbf7d0;padding:3px 8px;border-radius:12px;font-weight:800;'
            'font-size:10px;">Outstanding</span>'
        )
    if score >= 70:
        return (
            '<span style="background:#f3edff;color:#6d3ec4;border:1px solid '
            '#e7dff2;padding:3px 8px;border-radius:12px;font-weight:800;'
            'font-size:10px;">Good</span>'
        )
    return (
        '<span style="background:#fef3c7;color:#b45309;border:1px solid '
        '#fde68a;padding:3px 8px;border-radius:12px;font-weight:800;'
        'font-size:10px;">Needs Attention</span>'
    )


def _build_hod_staff_records(staff_users, labels_by_user_id=None):
    """Tukar senarai staf sebenar kepada baris jadual HOD.

    Semua nilai datang daripada ``get_module_scores`` melalui
    ``_build_hod_module_scorecards``. Tiada nama, gred atau markah rekaan.
    """
    staff_users = list(staff_users)
    users_by_username = {staff.username: staff for staff in staff_users}
    _, summaries = _build_hod_module_scorecards(
        staff_users,
        labels_by_user_id=labels_by_user_id,
    )

    records = []
    for summary in summaries:
        staff = users_by_username.get(summary['username'])
        profile = getattr(staff, 'profile', None) if staff else None
        department_label = (
            profile.get_department_display()
            if profile is not None
            else 'Unknown Department'
        )
        records.append({
            'name': summary['name'],
            'username': summary['username'],
            'label': summary['label'],
            'clinic': (
                getattr(profile, 'assigned_facility', None)
                if profile is not None else None
            ) or 'Unassigned',
            'state': (
                getattr(profile, 'assigned_state', None)
                if profile is not None else None
            ) or 'Unassigned',
            'department': department_label,
            'grade': department_label,
            'score': summary['module_average'],
            'tier': summary['tier'],
            'clinical_score': summary['clinical_score'],
            'operational_score': summary['operational_score'],
            'interpersonal_score': summary['interpersonal_score'],
            'module_scores': summary['module_scores'],
            'scorable_modules': summary['scorable_modules'],
            'status': _score_badge_html(summary['module_average']),
        })
    return records


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

    # --- SATU SUMBER SKOR: get_module_scores() UNTUK SEMUA STAF HOD ---
    active_facilities = len(klinik_dipaparkan)
    dept = getattr(request.user.profile, 'department', 'FMS')
    senarai_doktor, staff_by_label = _build_hod_staff_picker(
        dept, pilihan_negeri, pilihan_klinik
    )
    staff_users = list(staff_by_label.values())
    hod_module_scorecards, hod_staff_summaries = _build_hod_module_scorecards(
        staff_users,
        labels_by_user_id={
            staff.id: label for label, staff in staff_by_label.items()
        },
    )

    requested_officer = request.GET.get('officer')
    if requested_officer not in staff_by_label:
        requested_officer = senarai_doktor[0] if senarai_doktor else None
    pilihan_doktor = requested_officer
    selected_hod_staff = next(
        (
            summary for summary in hod_staff_summaries
            if summary['label'] == pilihan_doktor
        ),
        None,
    )

    sorted_summaries = sorted(
        hod_staff_summaries,
        key=lambda item: (
            item['module_average'] is not None,
            item['module_average'] or 0,
        ),
        reverse=True,
    )
    hod_officer_preview = sorted_summaries[:3]
    top_staff_summary = sorted_summaries[0] if sorted_summaries else None

    total_staff = len(staff_users)
    evaluations_completed = sum(
        summary['scorable_modules'] for summary in hod_staff_summaries
    )
    avg_performance_value = _mean_score(
        summary['module_average'] for summary in hod_staff_summaries
    )
    avg_performance = (
        int(round(avg_performance_value))
        if avg_performance_value is not None else None
    )
    fast_track_count = sum(
        1 for summary in hod_staff_summaries
        if summary['module_average'] is not None
        and summary['module_average'] >= 85
    )

    staff_ids = [staff.id for staff in staff_users]
    pending_validation_count = 0
    if staff_ids:
        pending_validation_count = (
            ProcedureLog.objects.filter(
                officer_id__in=staff_ids, is_verified=False
            ).count()
            + CPDClaim.objects.filter(
                officer_id__in=staff_ids, status='PENDING'
            ).count()
            + PolicyDeclaration.objects.filter(
                officer_id__in=staff_ids, is_verified=False
            ).count()
        )

    # A. Roster sebenar (tiada mock doctor dalam skop HOD).
    from django.urls import reverse

    clinical_url = reverse('cts_module')
    roster_rows = []
    for summary in sorted_summaries:
        officer_link = (
            f"{clinical_url}?officer={urllib.parse.quote(summary['label'])}"
            f"&clinic={urllib.parse.quote(pilihan_klinik or '')}"
            f"&state={urllib.parse.quote(pilihan_negeri or '')}"
            "&tab=Skills"
        )
        score_display = (
            summary['module_average']
            if summary['module_average'] is not None else 'No Data'
        )
        roster_rows.append({
            'Officer Name': (
                f"<a href='{officer_link}' style='color:#6d3ec4; "
                "font-weight:800; text-decoration:none;'>"
                f"{summary['name']}</a>"
            ),
            'Grade': 'UD43 (MO)',
            'Current Score': score_display,
            'Status': (
                "<span class='status-badge status-excellent'>"
                f"{summary['tier']}</span>"
            ),
        })
    if roster_rows:
        roster_html = pd.DataFrame(roster_rows).to_html(
            index=False, classes="roster-table", escape=False
        )
    else:
        roster_html = (
            "<div style='text-align:center; padding: 20px; color:#a3aed1;'>"
            "No officers found for this facility scope.</div>"
        )

    # Dapatkan nama dinamik untuk header
    hod_name = f"{request.user.first_name} {request.user.last_name}".strip() or request.user.username
    hod_facility_label = _hod_scope_label(hod_scope, hod_state, hod_facility)

    # B. CARTA BAR KPI JABATAN (DATA SEBENAR DARI get_module_scores)
    kpi_categories = ["Clinical Excellence", "CPD & Knowledge", "Doc Audit", "Policy Compliance"]
    kpi_targets = [85, 80, 90, 100]
    score_by_key = {
        card['key']: card['score'] for card in hod_module_scorecards
    }

    def _rounded_card_score(key):
        value = score_by_key.get(key)
        return int(round(value)) if value is not None else None

    actual_clinical = _rounded_card_score('clinical_delivery')
    actual_cpd = _rounded_card_score('cpd')
    actual_doc = _rounded_card_score('documentation')
    actual_policy = _rounded_card_score('policies')

    # Tapis pegawai berdasarkan klinik/fasiliti semasa HOD
    staff_for_kpi = staff_users

    # === CUSUM SHADOW MONITORING DATA (READ-ONLY SNAPSHOT) ===
    cusum_snapshots_qs = CusumSnapshot.objects.filter(
        module='documentation',
        officer__in=staff_for_kpi,
    ).select_related('officer').order_by('officer__first_name', 'officer__username')

    cusum_snapshots = []
    in_control_cnt = 0
    watch_cnt = 0
    alert_cnt = 0
    insufficient_cnt = 0

    for snap in cusum_snapshots_qs:
        st = snap.state
        if st == 'IN_CONTROL':
            in_control_cnt += 1
        elif st == 'WATCH':
            watch_cnt += 1
        elif st == 'ALERT':
            alert_cnt += 1
        else:
            insufficient_cnt += 1

        fname = f"Dr. {snap.officer.first_name} {snap.officer.last_name}".strip()
        disp_name = fname if fname != "Dr." else f"Dr. {snap.officer.username}"

        cusum_snapshots.append({
            'officer_name': disp_name,
            'username': snap.officer.username,
            'state': snap.state,
            'state_label': snap.get_state_display(),
            'cusum_value': snap.cusum_value,
            'alert_count': snap.alert_count,
            'observation_count': snap.observation_count,
            'skipped_count': snap.skipped_count,
            'last_event_at': snap.last_event_at,
            'last_alert_at': snap.last_alert_at,
            'computed_at': snap.computed_at,
        })

    cusum_summary = {
        'total': len(cusum_snapshots),
        'in_control': in_control_cnt,
        'watch': watch_cnt,
        'alert': alert_cnt,
        'insufficient_data': insufficient_cnt,
    }

    # Open episodes bagi tujuan audit alert
    cusum_open_episode_count = CusumEpisode.objects.filter(
        module='documentation',
        officer__in=staff_for_kpi,
        status='OPEN',
    ).count()

    cusum_alert_count = sum(1 for s in cusum_snapshots if s['alert_count'] > 0)
    top_alerts = [s for s in cusum_snapshots if s['alert_count'] > 0]
    top_alerts.sort(key=lambda x: x['last_alert_at'] or timezone.now() - timedelta(days=9999), reverse=True)
    cusum_top_alerts = top_alerts[:5]
    action_urgent_count = cusum_alert_count
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
        text=[f"{v}%" if v is not None else "No Data" for v in kpi_actuals], textposition='auto',
        textfont=dict(color='#ffffff', size=11, family='Inter', weight='bold'),
        hovertemplate="<b>%{x}</b><br>Actual: %{text}<extra></extra>"
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

    # C. MO COMPETENCY HEATMAP (DATA SEBENAR DARI get_module_scores)
    mo_names = []
    z_mo_data = []

    # Ambil 5 staf teratas (berdasarkan purata 10 modul) untuk heatmap.
    for summary in sorted_summaries[:5]:
        mo_names.append(summary['name'])
        z_mo_data.append([
            int(round(summary['clinical_score']))
            if summary['clinical_score'] is not None else 0,
            int(round(summary['operational_score']))
            if summary['operational_score'] is not None else 0,
            int(round(summary['interpersonal_score']))
            if summary['interpersonal_score'] is not None else 0,
        ])

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
    calendar_today = timezone.localdate()

    # 1. Tuntutan CPD Menunggu Pengesahan HOD (dalam skop staf HOD sahaja)
    for cpd in CPDClaim.objects.filter(
        officer_id__in=staff_ids, status='PENDING'
    ).order_by('submitted_at'):
        ev_day = (
            cpd.submitted_at.day
            if hasattr(cpd, 'submitted_at') and cpd.submitted_at
            else calendar_today.day
        )
        ev_date = (
            cpd.submitted_at.strftime('%Y-%m-%d')
            if hasattr(cpd, 'submitted_at') and cpd.submitted_at
            else calendar_today.strftime('%Y-%m-%d')
        )
        c_name = f"{cpd.officer.first_name} {cpd.officer.last_name}".strip() or cpd.officer.username
        hod_calendar_events.append({
            'title': f"CPD Claim: Dr. {c_name} ({cpd.points} Pts)",
            'date': ev_date,
            'day': ev_day,
            'type': 'pending',
            'desc': f"Topic: {cpd.topic}"
        })

    # 2. Amaran Dokumentasi Lewat dari Staf (dalam skop staf HOD sahaja)
    for ddoc in DocumentationLog.objects.filter(
        officer_id__in=staff_ids, is_ontime=False
    ).order_by('-created_at')[:4]:
        ev_day = (
            ddoc.created_at.day
            if hasattr(ddoc, 'created_at') and ddoc.created_at
            else calendar_today.day
        )
        ev_date = (
            ddoc.created_at.strftime('%Y-%m-%d')
            if hasattr(ddoc, 'created_at') and ddoc.created_at
            else calendar_today.strftime('%Y-%m-%d')
        )
        d_name = f"{ddoc.officer.first_name} {ddoc.officer.last_name}".strip() or ddoc.officer.username
        hod_calendar_events.append({
            'title': f"Late Doc Audit: Dr. {d_name}",
            'date': ev_date,
            'day': ev_day,
            'type': 'duty',
            'desc': f"Patient ID: {ddoc.patient_id} ({ddoc.delay_hours}h delay)"
        })

    # 3. Pegawai Berpotensi Fast-Track (purata modul sebenar >= 85%)
    for summary in hod_staff_summaries:
        module_average = summary['module_average']
        if module_average is None or module_average < 85:
            continue
        hod_calendar_events.append({
            'title': f"Fast-Track Review: {summary['name']}",
            'date': calendar_today.strftime('%Y-%m-%d'),
            'day': calendar_today.day,
            'type': 'fast-track',
            'desc': (
                f"Module Average: {module_average:.1f}% "
                f"({summary['tier']})"
            )
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
        'kpi_actuals': kpi_actuals,
        'hod_module_scorecards': hod_module_scorecards,
        'hod_staff_summaries': hod_staff_summaries,
        'hod_officer_preview': hod_officer_preview,
        'selected_hod_staff': selected_hod_staff,
        'top_staff_summary': top_staff_summary,
        'fast_track_count': fast_track_count,
        'pending_validation_count': pending_validation_count,
        'action_urgent_count': action_urgent_count,
        'senarai_doktor': senarai_doktor,
        'pilihan_doktor': pilihan_doktor,

        # --- DATA SEBENAR CUSUM SHADOW MODE (HOD) ---
        'cusum_snapshots': cusum_snapshots,
        'cusum_summary': cusum_summary,
        'cusum_alert_count': cusum_alert_count,
        'cusum_open_episode_count': cusum_open_episode_count,
        'cusum_top_alerts': cusum_top_alerts,
    }
    return render(request, 'dashboard/hod_dashboard.html', context)

def _compute_hba1c_cusum_surveillance(target_user, dept, pilihan_doktor):
    """
    Enjin Pengiraan CUSUM Klinikal Berasaskan Spesifikasi KKM Indicator-1:
    - Kriteria Inklusi: Pesakit T2DM dengan bacaan asas HbA1c > 10.0% (atau kohort T2DM tidak terkawal).
    - Kriteria Eksklusi: T1DM, GDM / Kehamilan, Severe Mental Illness, Palliative Care.
    - Sasaran 6 Bulan (183 hari): Pengurangan HbA1c >= 1.0% ATAU mencapai HbA1c <= 7.0%.
    - Sasaran KKM: >= 80% pesakit layak mencapai sasaran (p0=0.20, p1=0.35, h=4.5).
    """
    from .cusum import CusumParameters, bernoulli_cusum

    today_dt = timezone.localdate()
    params = CusumParameters(p0=0.20, p1=0.35, h=4.5)

    evaluated_cohort = []
    pending_cohort = []
    excluded_count = 0
    cohort_mode_label = "Indicator-1 Cohort (Baseline HbA1c > 10.0%)"

    if target_user:
        procs = list(
            ProcedureLog.objects.filter(officer=target_user)
            .prefetch_related('doc_logs')
            .order_by('procedure_date', 'id')
        )

        # Kumpulkan lawatan mengikut pesakit yang sama (patient_ref / MRN)
        patient_groups = {}
        for p in procs:
            linked_docs = list(p.doc_logs.all())
            doc_label = linked_docs[0].patient_id if linked_docs else ''
            pt_key = p.patient_ref or doc_label or f"CASE-{p.id:04d}"
            if pt_key not in patient_groups:
                patient_groups[pt_key] = {
                    'patient_ref': p.patient_ref or (doc_label.split(' (')[0].strip() if doc_label else pt_key),
                    'visits': [],
                }
            patient_groups[pt_key]['visits'].append(p)

        eligible_patients = []
        for pt_key, pdata in patient_groups.items():
            visits = pdata['visits']
            # Semak Kriteria Eksklusi (T1DM, GDM, Severe Mental Illness, Palliative Care)
            is_excluded = False
            for v in visits:
                diag_blob = f"{v.primary_diagnosis or ''} {v.procedure_name or ''}".lower()
                comorb_list = [str(c).lower() for c in (v.comorbidities or [])]
                if (
                    't1dm' in diag_blob
                    or 'type 1' in diag_blob
                    or 'gdm' in diag_blob
                    or 'gestational' in diag_blob
                    or any('severe mental' in c or 'palliative' in c for c in comorb_list)
                ):
                    is_excluded = True
                    break

            if is_excluded:
                excluded_count += 1
                continue

            hba1c_visits = [v for v in visits if v.hba1c_percent is not None]
            if not hba1c_visits:
                continue

            eligible_patients.append({
                'patient_ref': pdata['patient_ref'],
                'hba1c_visits': hba1c_visits,
            })

        # Utamakan kohort tepat Indicator-1 (Baseline HbA1c > 10.0%);
        # jika belum ada kes > 10.0%, nilai semua kohort T2DM tidak terkawal (HbA1c > 7.0%)
        strict_cohort = [
            ep for ep in eligible_patients
            if float(ep['hba1c_visits'][0].hba1c_percent) > 10.0
        ]
        if strict_cohort:
            active_cohort = strict_cohort
            cohort_mode_label = "Indicator-1 Cohort (Baseline HbA1c > 10.0%)"
        else:
            active_cohort = [
                ep for ep in eligible_patients
                if float(ep['hba1c_visits'][0].hba1c_percent) > 7.0
            ]
            cohort_mode_label = "T2DM Uncontrolled Cohort (Baseline HbA1c > 7.0%)"

        # Susun mengikut tarikh baseline untuk urutan siri masa CUSUM
        active_cohort.sort(key=lambda item: (item['hba1c_visits'][0].procedure_date, item['hba1c_visits'][0].id))

        for ep in active_cohort:
            v_list = ep['hba1c_visits']
            v_base = v_list[0]
            base_date = v_base.procedure_date
            base_hba1c = float(v_base.hba1c_percent)

            # Cari bacaan susulan dalam tempoh 6 bulan (<= 183 hari) selepas bacaan asas
            within_6m = [
                v for v in v_list[1:]
                if 0 < (v.procedure_date - base_date).days <= 183
            ]
            # Susulan selepas 183 hari tidak dikira sebagai kejayaan;
            # pesakit tersebut akan ditandakan overdue di bawah.
            followup_visits = within_6m

            if followup_visits:
                # Ambil bacaan terbaik dalam tempoh pemantauan 6 bulan
                best_fu = min(followup_visits, key=lambda x: float(x.hba1c_percent))
                fu_hba1c = float(best_fu.hba1c_percent)
                reduction = round(base_hba1c - fu_hba1c, 2)
                days_diff = (best_fu.procedure_date - base_date).days
                achieved = (reduction >= 1.0) or (fu_hba1c <= 7.0)

                evaluated_cohort.append({
                    'patient_ref': ep['patient_ref'],
                    'baseline_date': base_date.strftime('%d %b %Y'),
                    'baseline_hba1c': base_hba1c,
                    'followup_date': best_fu.procedure_date.strftime('%d %b %Y'),
                    'followup_hba1c': fu_hba1c,
                    'reduction': reduction,
                    'days_diff': days_diff,
                    'achieved': achieved,
                    'is_failure': not achieved,
                    'status_label': (
                        f"Achieved (-{reduction:.1f}%)" if reduction >= 1.0
                        else ("Target ≤7.0% Reached" if fu_hba1c <= 7.0 else f"Not Met ({reduction:+.1f}%)")
                    ),
                })
            else:
                days_elapsed = (today_dt - base_date).days
                if days_elapsed > 183:
                    # Melepasi 6 bulan tanpa rekod penurunan HbA1c >= 1%
                    evaluated_cohort.append({
                        'patient_ref': ep['patient_ref'],
                        'baseline_date': base_date.strftime('%d %b %Y'),
                        'baseline_hba1c': base_hba1c,
                        'followup_date': 'Overdue (>6 Mo)',
                        'followup_hba1c': None,
                        'reduction': 0.0,
                        'days_diff': days_elapsed,
                        'achieved': False,
                        'is_failure': True,
                        'status_label': 'Overdue (>183 Days No 6M HbA1c)',
                    })
                else:
                    pending_cohort.append({
                        'patient_ref': ep['patient_ref'],
                        'baseline_date': base_date.strftime('%d %b %Y'),
                        'baseline_hba1c': base_hba1c,
                        'days_remaining': max(0, 183 - days_elapsed),
                    })

    # Tiada data sandaran rekaan: jika DB belum mempunyai pasangan HbA1c
    # 6 bulan, paparkan status NO EVALUATED COHORT secara jujur.
    outcomes = [item['is_failure'] for item in evaluated_cohort]
    cusum_res = bernoulli_cusum(outcomes, params)

    numerator = sum(1 for item in evaluated_cohort if item['achieved'])
    denominator = len(evaluated_cohort)
    attainment_rate = round((numerator / denominator) * 100.0, 1) if denominator > 0 else 0.0

    state_display_map = {
        'INSUFFICIENT_DATA': 'NO EVALUATED COHORT',
        'IN_CONTROL': 'IN CONTROL (≥80% Target)',
        'WATCH': 'WATCH (Drift Detected)',
        'ALERT': 'ALERT (Action Required)',
    }
    status_text = state_display_map.get(cusum_res.state, cusum_res.state)
    clin_drift = cusum_res.state in ('WATCH', 'ALERT')

    # Lukis Carta One-Sided Bernoulli CUSUM dengan gradien warna asal (Hijau Bawah -> Kuning -> Merah Atas)
    fig = go.Figure()
    fig.add_hline(y=params.h, line_dash="dot", line_color="#dc2626", annotation_text=f"Alert Limit (h = {params.h})")
    fig.add_hline(y=params.watch_threshold, line_dash="dash", line_color="#d97706", annotation_text=f"Watch Threshold ({params.watch_threshold})")
    fig.add_hline(y=0.0, line_dash="solid", line_color="#16a34a", annotation_text="Target Baseline (≥80% Achieved)")

    if cusum_res.steps:
        x_seq = [s.sequence for s in cusum_res.steps]
        y_cusum = [round(s.cusum_value, 3) for s in cusum_res.steps]
        hover_texts = []
        for idx_s, step in enumerate(cusum_res.steps):
            pt_info = evaluated_cohort[idx_s]
            fu_txt = f"{pt_info['followup_hba1c']:.1f}%" if pt_info['followup_hba1c'] is not None else "No 6M HbA1c"
            hover_texts.append(
                f"<b>{pt_info['patient_ref']}</b><br>"
                f"HbA1c: {pt_info['baseline_hba1c']:.1f}% → {fu_txt}<br>"
                f"Outcome: {pt_info['status_label']}<br>"
                f"CUSUM (S_n): {step.cusum_value:.3f}"
            )

        fig.add_trace(go.Scatter(
            x=x_seq,
            y=y_cusum,
            mode='lines+markers',
            name='HbA1c Bernoulli CUSUM (S_n)',
            line=dict(color='#150a35', width=3.5, shape='spline'),
            marker=dict(
                size=10,
                color=['#dc2626' if s.is_failure else '#16a34a' for s in cusum_res.steps],
                line=dict(color='white', width=2),
            ),
            fill='tozeroy',
            fillcolor='rgba(109, 62, 196, 0.12)',
            text=hover_texts,
            hovertemplate="%{text}<extra></extra>",
        ))
        y_top = max(5.2, max(y_cusum) + 0.8)
    else:
        fig.add_trace(go.Scatter(
            x=["Awaiting 6M Follow-Up"], y=[0.0], mode='markers',
            marker=dict(size=9, color='#94a3b8'), name='Awaiting Follow-Up'
        ))
        y_top = 5.2

    fig.update_layout(
        title=dict(
            text=f"<b>HbA1c ≥1% Reduction CUSUM Surveillance • {pilihan_doktor}</b><br>"
                 f"<span style='font-size:11px; color:#6f628b;'>KKM Indicator-1: Optimization of Diabetic Control (6-Month Window • Target ≥80%)</span>",
            font=dict(size=14, color="#150a35"),
        ),
        xaxis_title="Sequential Evaluated Diabetic Patients (N)",
        yaxis_title="HbA1c Failure CUSUM (S_n)",
        height=330,
        margin=dict(t=55, l=45, r=25, b=45),
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        xaxis=dict(showgrid=True, gridcolor='#e7dff2', dtick=2),
        yaxis=dict(range=[-0.2, y_top], showgrid=True, gridcolor='#e7dff2'),
        showlegend=False,
    )
    chart_html = fig.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False})

    return {
        'chart_html': chart_html,
        'numerator': numerator,
        'denominator': denominator,
        'attainment_rate': attainment_rate,
        'status_text': status_text,
        'cusum_state': cusum_res.state,
        'cusum_value': round(cusum_res.cusum_value, 3),
        'clin_drift': clin_drift,
        'evaluated_cohort': evaluated_cohort,
        'pending_cohort': pending_cohort,
        'excluded_count': excluded_count,
        'cohort_mode_label': cohort_mode_label,
    }


# --- LOGIK MODUL KLINIKAL (CTS / FMS) LENGKAP ---
@login_required(login_url='/login/')
def cts_view(request):
    
    if request.method == 'POST':
        form_type = (request.POST.get('form_type') or '').strip()

        # 1. Hantar Feedback / Peringatan SLA / Spot Audit ke Staf
        if form_type == 'send_hod_feedback':
            target_username = (request.POST.get('target_username') or '').strip()
            message_content = (request.POST.get('message_text') or '').strip()
            mod_ref = (request.POST.get('module_ref') or 'Clinical Excellence').strip()
            severity = (request.POST.get('severity') or 'WARNING').strip()

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
                    message_text=message_content,
                )
                disp_doc = f"Dr. {target_user.first_name} {target_user.last_name}".strip()
                if disp_doc == "Dr.":
                    disp_doc = f"Dr. {target_user.username}"
                messages.success(request, f"✅ Mesej / peringatan berjaya dihantar kepada {disp_doc}.")
            else:
                messages.error(request, "❌ Gagal menghantar mesej. Pegawai tidak ditemui atau catatan kosong.")
            return redirect(request.get_full_path())

        # 2. Pengesahan Rekod Klinikal / Prosedur oleh HOD
        elif form_type == 'verify_procedure_log':
            proc_id = _parse_int(request.POST.get('procedure_id'), 0)
            proc_obj = ProcedureLog.objects.filter(id=proc_id).first()
            if proc_obj and not proc_obj.is_verified:
                proc_obj.is_verified = True
                proc_obj.hod_verifier = request.user
                proc_obj.verified_at = timezone.now()
                proc_obj.save(update_fields=['is_verified', 'hod_verifier', 'verified_at'])
                messages.success(request, f"✅ Rekod klinikal '{proc_obj.procedure_name}' telah disahkan.")
            elif proc_obj:
                messages.info(request, "ℹ️ Rekod klinikal ini telah disahkan sebelum ini.")
            else:
                messages.error(request, "❌ Rekod klinikal tidak ditemui.")
            return redirect(request.get_full_path())

        # 3. Kelulusan / Penolakan Tuntutan CPD (myCPD 2.0) oleh HOD
        elif form_type == 'review_cpd_claim':
            claim_id = _parse_int(request.POST.get('claim_id'), 0)
            decision = (request.POST.get('decision') or '').strip().upper()
            rejection_reason = (request.POST.get('rejection_reason') or '').strip()
            claim_obj = CPDClaim.objects.filter(id=claim_id).first()

            if claim_obj and decision in ('APPROVED', 'REJECTED'):
                claim_obj.status = decision
                claim_obj.verified_by = request.user
                claim_obj.verified_at = timezone.now()
                if decision == 'REJECTED':
                    claim_obj.rejection_reason = rejection_reason or 'Dokumen sokongan tidak mencukupi.'
                else:
                    claim_obj.rejection_reason = None
                claim_obj.save(update_fields=['status', 'verified_by', 'verified_at', 'rejection_reason'])
                status_word = "diluluskan" if decision == 'APPROVED' else "ditolak"
                messages.success(request, f"✅ Tuntutan CPD '{claim_obj.topic}' ({claim_obj.points} pts) telah {status_word}.")
            else:
                messages.error(request, "❌ Tuntutan CPD tidak ditemui atau tindakan tidak sah.")
            return redirect(request.get_full_path())

        # 4. Pengesahan Rekod Audit Polisi oleh HOD
        elif form_type == 'verify_policy_declaration':
            decl_id = _parse_int(request.POST.get('declaration_id'), 0)
            hod_remarks = (request.POST.get('hod_remarks') or '').strip()
            decl_obj = PolicyDeclaration.objects.filter(id=decl_id).first()
            if decl_obj:
                decl_obj.is_verified = True
                if hod_remarks:
                    decl_obj.hod_remarks = hod_remarks
                decl_obj.save(update_fields=['is_verified', 'hod_remarks'])
                messages.success(request, "✅ Rekod audit pematuhan polisi & protokol telah disahkan oleh HOD.")
            else:
                messages.error(request, "❌ Rekod audit polisi tidak ditemui.")
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
    hod_facility_label = _hod_scope_label(hod_scope, hod_state, hod_facility)

    # --- 3. PICKER DOKTOR: STAF SEBENAR SAHAJA (TIADA MOCK) ---
    senarai_doktor, staff_by_label = _build_hod_staff_picker(
        dept, pilihan_negeri, pilihan_klinik
    )
    staff_list = list(staff_by_label.values())
    _, hod_summaries = _build_hod_module_scorecards(
        staff_list,
        labels_by_user_id={
            staff.id: label for label, staff in staff_by_label.items()
        },
    )

    pilihan_doktor = request.GET.get('officer')
    if not pilihan_doktor or pilihan_doktor not in senarai_doktor:
        pilihan_doktor = senarai_doktor[0] if senarai_doktor else "Tiada Pegawai"

    # --- PENGIRAAN STATISTIK DINAMIK (DATA SEBENAR) ---
    active_facilities = len(klinik_tersedia) if pilihan_klinik == "All Clinics" else 1
    total_staff = len(senarai_doktor)
    avg_performance = int(round(
        _mean_score(
            summary['module_average'] for summary in hod_summaries
        ) or 0
    ))
    evaluations_completed = sum(
        summary['scorable_modules'] for summary in hod_summaries
    )

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

    target_user = staff_by_label.get(pilihan_doktor)
    if not target_user:
        doc_username_clean = pilihan_doktor.replace("🟢", "").replace("Dr.", "").split(" (")[0].strip()
        target_user = User.objects.filter(username__icontains=doc_username_clean).first()
        if not target_user:
            target_user = User.objects.filter(first_name__icontains=doc_username_clean).first()

    # Pembolehubah Konteks 4 Tab Clinical HOD
    pending_procedures = []
    recent_procedures = []
    clin_cusum_drift = False
    doc_metrics = {
        'total_entries': 0,
        'completed_count': 0,
        'pending_count': 0,
        'ontime_pct': 0,
        'avg_time_display': '—',
        'cusum_state': 'INSUFFICIENT_DATA',
        'cusum_state_label': 'No Snapshot',
        'cusum_value': 0.0,
        'cusum_alerts': 0,
        'needs_reminder': False,
    }
    policy_review = {
        'has_data': False,
        'declaration': None,
        'domains': [],
        'items': [],
        'critical_misses': [],
        'overall_pct': 0,
    }
    cpd_metrics = {
        'approved_pts': 0,
        'pending_pts': 0,
        'pending_count': 0,
        'target_pts': 50,
    }
    pending_cpd_claims = []
    all_cpd_claims = []
    cpd_trajectory = None
    cpd_score = None

    # ==========================================
    # TAB 1: CLINICAL DELIVERY (INDICATOR-1 HbA1c >= 1% REDUCTION CUSUM)
    # ==========================================
    hba1c_cusum_data = {}
    if active_tab == 'Skills':
        if target_user:
            all_procs_qs = ProcedureLog.objects.filter(officer=target_user).order_by('procedure_date', 'id')
            pending_procedures = list(all_procs_qs.filter(is_verified=False).order_by('-procedure_date', '-id')[:10])
            recent_procedures = list(all_procs_qs.order_by('-procedure_date', '-id')[:8])

        hba1c_cusum_data = _compute_hba1c_cusum_surveillance(target_user, dept, pilihan_doktor)
        chart_html = hba1c_cusum_data['chart_html']
        fms_total_patients = hba1c_cusum_data['denominator']
        fms_kadar_kawalan = hba1c_cusum_data['attainment_rate']
        fms_prestasi = hba1c_cusum_data['status_text']
        clin_cusum_drift = hba1c_cusum_data['clin_drift']

    # ==========================================
    # TAB 2: DOCUMENTATION (4-HOUR SLA & CUSUM SHADOW SURVEILLANCE)
    # ==========================================
    elif active_tab == 'Documentation':
        total_logs = 0
        ontime_pct, delayed_pct, overdue_pct = 0, 0, 0
        delayed_html = "<div style='color:#16a34a; font-weight:700; font-size:12.5px; padding:12px; background:#f0fdf4; border:1px solid #bbf7d0; border-radius:10px;'>✓ Tiada nota dokumentasi lewat atau tertunggak bagi pegawai ini.</div>"

        if target_user:
            doc_logs = DocumentationLog.objects.filter(officer=target_user)
            total_logs = doc_logs.count()
            completed_qs = doc_logs.filter(is_completed=True)
            completed_cnt = completed_qs.count()
            pending_cnt = doc_logs.filter(is_completed=False).count()

            snap_doc = CusumSnapshot.objects.filter(officer=target_user, module='documentation').first()
            c_state = snap_doc.state if snap_doc else 'INSUFFICIENT_DATA'
            c_label = snap_doc.get_state_display() if snap_doc else 'No Snapshot'
            c_val = snap_doc.cusum_value if snap_doc else 0.0
            c_alerts = snap_doc.alert_count if snap_doc else 0

            if completed_cnt > 0:
                ontime_count = completed_qs.filter(is_ontime=True).count()
                delayed_count = completed_qs.filter(is_ontime=False, delay_hours__lte=24).count()
                overdue_count = completed_qs.filter(is_ontime=False, delay_hours__gt=24).count()

                ontime_pct = int(round((ontime_count / completed_cnt) * 100))
                delayed_pct = int(round((delayed_count / completed_cnt) * 100))
                overdue_pct = int(round((overdue_count / completed_cnt) * 100))

                avg_hrs = sum(float(d.delay_hours or 0) for d in completed_qs) / completed_cnt
                hrs_part = int(avg_hrs)
                mins_part = int(round((avg_hrs - hrs_part) * 60))
                avg_time_disp = f"{hrs_part}h {mins_part:02d}m"
            else:
                delayed_count = 0
                overdue_count = 0
                avg_time_disp = "—"

            needs_rem = (pending_cnt > 0) or (delayed_count + overdue_count > 0) or (c_state in ('WATCH', 'ALERT'))
            doc_metrics = {
                'total_entries': total_logs,
                'completed_count': completed_cnt,
                'pending_count': pending_cnt,
                'ontime_pct': ontime_pct,
                'avg_time_display': avg_time_disp,
                'cusum_state': c_state,
                'cusum_state_label': c_label,
                'cusum_value': c_val,
                'cusum_alerts': c_alerts,
                'needs_reminder': needs_rem,
            }

            attention_qs = doc_logs.filter(Q(is_ontime=False, is_completed=True) | Q(is_completed=False)).order_by('is_completed', '-delay_hours')[:8]
            if attention_qs.exists():
                rows = []
                for d in attention_qs:
                    if not d.is_completed:
                        status_str = "<span style='color:#d97706; font-weight:800;'>Pending Sign-Off</span>"
                        delay_str = "Awaiting Sign-Off"
                        reason_str = d.encounter_type or "Outpatient note not yet signed"
                    else:
                        status_str = "<span style='color:#dc2626; font-weight:800;'>Exceeded 4H SLA</span>"
                        delay_str = f"{d.delay_hours:.1f} Hours"
                        reason_str = d.reason_for_delay or "No justification provided"
                    rows.append({
                        "Patient / MRN": d.patient_id,
                        "Status": status_str,
                        "Turnaround": delay_str,
                        "Justification / Note": reason_str,
                        "Date": d.created_at.strftime("%d/%m/%Y"),
                    })
                delayed_html = pd.DataFrame(rows).to_html(index=False, classes="custom-table", escape=False)
        else:
            total_logs = 20
            ontime_pct, delayed_pct, overdue_pct = 85, 10, 5
            doc_metrics = {
                'total_entries': 20,
                'completed_count': 20,
                'pending_count': 0,
                'ontime_pct': 85,
                'avg_time_display': '1h 45m',
                'cusum_state': 'IN_CONTROL',
                'cusum_state_label': 'In Control',
                'cusum_value': 0.1200,
                'cusum_alerts': 0,
                'needs_reminder': True,
            }
            df_delayed = pd.DataFrame({
                "Patient / MRN": ["RN-88210", "RN-88214"],
                "Status": ["Exceeded 4H SLA", "Exceeded 4H SLA"],
                "Turnaround": ["5.5 Hours", "4.4 Hours"],
                "Justification / Note": ["Ward emergency referral", "High patient workload"],
                "Date": [date.today().strftime("%d/%m/%Y"), (date.today() - timedelta(days=2)).strftime("%d/%m/%Y")]
            })
            delayed_html = df_delayed.to_html(index=False, classes="custom-table", escape=False)

        fig_time = go.Figure()
        if ontime_pct > 0:
            fig_time.add_trace(go.Bar(y=["Patient Records"], x=[ontime_pct], name="On-Time (≤4H)", orientation='h', marker=dict(color="#16a34a"), text=f"{ontime_pct}%", textposition="inside", insidetextanchor="middle"))
        if delayed_pct > 0:
            fig_time.add_trace(go.Bar(y=["Patient Records"], x=[delayed_pct], name="Delayed (4–24H)", orientation='h', marker=dict(color="#d97706"), text=f"{delayed_pct}%", textposition="inside", insidetextanchor="middle"))
        if overdue_pct > 0:
            fig_time.add_trace(go.Bar(y=["Patient Records"], x=[overdue_pct], name="Overdue (>24H)", orientation='h', marker=dict(color="#dc2626"), text=f"{overdue_pct}%", textposition="inside", insidetextanchor="middle"))

        if total_logs == 0 or (ontime_pct + delayed_pct + overdue_pct == 0):
            fig_time.add_trace(go.Bar(y=["Patient Records"], x=[100], name="No Completed Records", orientation='h', marker=dict(color="#e2e8f0"), text="0 Completed Records in DB", textposition="inside"))

        fig_time.update_layout(
            barmode='stack', height=135, margin=dict(l=0, r=0, t=10, b=0),
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            xaxis=dict(range=[0, 100], showgrid=False), yaxis=dict(showgrid=False),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        time_chart_html = fig_time.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False})

    # ==========================================
    # TAB 3: POLICIES & PROTOCOLS (REAL MICRO-AUDIT & DOMAIN A-E)
    # ==========================================
    elif active_tab == 'Policies':
        question_titles = {
            1: "Routine symptom screening (ASCVD, visual, neuro, renal, foot, sexual)",
            2: "Physical assessment & documentation (BMI, eye, foot, neuropathy)",
            3: "Underlying risk factors & family/obstetric history documented",
            4: "Review of current medications performed",
            5: "Medication adherence explicitly assessed",
            6: "Complementary / alternative medication use assessed",
            7: "Social barriers to chronic disease management evaluated",
            8: "Dilated retinal examination referral arranged",
            9: "Medical nutrition therapy (dietitian) referral arranged",
            10: "Self-management education support (DSMES) arranged",
            11: "HbA1c testing scheduled every 3–6 months",
            12: "Annual nephropathy screening (eGFR / Urine ACR) performed",
            13: "Baseline ECG ordered for high ASCVD risk patients",
            14: "Individualized glycemic targets discussed & agreed",
            15: "Therapy escalated timely if targets unmet after 3–6 months",
            16: "Hypoglycemic episodes routinely screened during visits",
            17: "Blood pressure optimized according to CPG targets",
            18: "Statin therapy initiated based on cardiovascular risk",
            19: "Preventive vaccinations (Influenza / Pneumococcal) recommended",
            20: "Autonomous management confidence in complex chronic cases",
        }

        latest_decl = (
            PolicyDeclaration.objects.filter(officer=target_user).order_by('-submitted_at', '-id').first()
            if target_user else None
        )

        if latest_decl:
            dom_list = [
                {'code': 'Domain A', 'name': 'Clinical Assessment', 'score': round(latest_decl.score_domain_a, 1)},
                {'code': 'Domain B', 'name': 'Risk Factors & Meds', 'score': round(latest_decl.score_domain_b, 1)},
                {'code': 'Domain C', 'name': 'Referrals & Follow-up', 'score': round(latest_decl.score_domain_c, 1)},
                {'code': 'Domain D', 'name': 'Labs & Diagnostics', 'score': round(latest_decl.score_domain_d, 1)},
                {'code': 'Domain E', 'name': 'Targets & Prevention', 'score': round(latest_decl.score_domain_e, 1)},
            ]
            item_rows = list(latest_decl.responses.order_by('question_number'))
            parsed_items = []
            misses = []
            for r in item_rows:
                q_title = question_titles.get(r.question_number, f"Protocol Item #{r.question_number}")
                pct_item = int(round((r.compliance_ratio or 0.0) * 100))
                parsed_items.append({
                    'num': r.question_number,
                    'title': q_title,
                    'is_compliant': r.is_compliant,
                    'pct': pct_item,
                })
                if not r.is_compliant:
                    misses.append(f"Question {r.question_number}: {q_title} ({pct_item}% compliance)")

            overall_pol = round(sum(d['score'] for d in dom_list) / 5.0, 1)
            policy_review = {
                'has_data': True,
                'declaration': latest_decl,
                'domains': dom_list,
                'items': parsed_items,
                'critical_misses': misses,
                'overall_pct': overall_pol,
            }

    # ==========================================
    # TAB 4: KNOWLEDGE & COMPETENCY (myCPD 2.0 APPROVAL & SURVEILLANCE)
    # ==========================================
    elif active_tab == 'Knowledge':
        cpd_score = None
        cpd_trajectory = None
        if target_user:
            cpd_claims_qs = CPDClaim.objects.filter(
                officer=target_user
            ).order_by('-submitted_at', '-event_date')
            all_cpd_claims = list(cpd_claims_qs)
            pending_cpd_claims = [
                claim for claim in all_cpd_claims
                if claim.status == 'PENDING'
            ]
            cpd_trajectory = build_cpd_trajectory(target_user)
            approved_pts = cpd_trajectory['points']['approved']
            pending_pts = cpd_trajectory['points']['pending']
            cpd_score = cpd_trajectory['score']['final']
            cpd_metrics = {
                'approved_pts': approved_pts,
                'pending_pts': pending_pts,
                'pending_count': cpd_trajectory['points']['pending_count'],
                'target_pts': cpd_trajectory['target_points'],
                'points_pct': cpd_trajectory['points']['pct'],
                'coverage_pct': cpd_trajectory['coverage']['weighted_pct'],
                'covered_count': cpd_trajectory['coverage']['covered_count'],
                'core_total': cpd_trajectory['coverage']['core_total'],
                'legacy_count': cpd_trajectory['legacy_ignored_count'],
                'status': cpd_trajectory['status'],
                'reason': cpd_trajectory['reason'],
            }
        else:
            cpd_metrics = {
                'approved_pts': 0,
                'pending_pts': 0,
                'pending_count': 0,
                'target_pts': 50,
                'points_pct': 0.0,
                'coverage_pct': 0.0,
                'covered_count': 0,
                'core_total': len(KKM_DOMAIN_CHOICES),
                'legacy_count': 0,
                'status': 'NO_DATA',
                'reason': 'Tiada staf dipilih.',
            }

        peer_score = avg_performance if avg_performance > 0 else 75

        def get_status_info(score):
            if score >= 85: return "Outstanding", "outstanding"
            elif score >= 75: return "Good", "good"
            elif score >= 65: return "Competent", "competent"
            else: return "Needs Attention", "needs-attention"

        if cpd_score is None:
            if cpd_metrics['status'] == 'PENDING_VERIFICATION':
                cpd_text, cpd_css_class = 'Pending Verification', 'needs-attention'
            else:
                cpd_text, cpd_css_class = 'No Data', 'needs-attention'
            gauge_cpd_value = 0
        else:
            cpd_text, cpd_css_class = get_status_info(cpd_score)
            gauge_cpd_value = cpd_score
        peer_text, peer_css_class = get_status_info(peer_score)

        fig_ind = go.Figure(go.Indicator(
            mode="gauge+number", value=gauge_cpd_value,
            title={'text': "CPD Composite Score 60/40 (%)", 'font': {'size': 15, 'color': '#150a35'}},
            number={'font': {'color': '#150a35'}},
            gauge={
                'axis': {'range': [0, 100], 'tickfont': {'color': '#150a35'}},
                'bar': {'color': "#150a35", 'thickness': 0.15},
                'steps': [{'range': [0, 65], 'color': "#fecdd3"}, {'range': [65, 75], 'color': "#fb7185"}, {'range': [75, 85], 'color': "#a577ff"}, {'range': [85, 100], 'color': "#6d3ec4"}]
            }
        ))
        fig_ind.update_layout(height=250, margin=dict(t=40, b=10, l=30, r=30), paper_bgcolor='rgba(0,0,0,0)')
        gauge_score_html = fig_ind.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False})

        fig_peer = go.Figure(go.Indicator(
            mode="gauge+number", value=peer_score,
            title={'text': "Facility Peer Benchmark (%)", 'font': {'size': 15, 'color': '#150a35'}},
            number={'font': {'color': '#150a35'}},
            gauge={
                'axis': {'range': [0, 100], 'tickfont': {'color': '#150a35'}},
                'bar': {'color': "#6d3ec4", 'thickness': 0.15},
                'steps': [{'range': [0, 65], 'color': "rgba(254, 205, 211, 0.5)"}, {'range': [65, 75], 'color': "rgba(251, 113, 133, 0.5)"}, {'range': [75, 85], 'color': "rgba(244, 63, 94, 0.5)"}, {'range': [85, 100], 'color': "rgba(190, 18, 60, 0.5)"}]
            }
        ))
        fig_peer.update_layout(height=250, margin=dict(t=40, b=10, l=30, r=30), paper_bgcolor='rgba(0,0,0,0)')
        gauge_peer_html = fig_peer.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False})

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
        'pilihan_doktor_username': (
            target_user.username if target_user else ''
        ),
        'chart_html': chart_html,
        'gauge_score_html': gauge_score_html,
        'gauge_peer_html': gauge_peer_html,
        'history_html': history_html,
        'time_chart_html': time_chart_html,
        'delayed_html': delayed_html,
        'cpd_text': cpd_text,
        'cpd_css_class': cpd_css_class,
        'peer_text': peer_text,
        'peer_css_class': peer_css_class,
        'active_facilities': active_facilities,
        'total_staff': total_staff,
        'avg_performance': avg_performance,
        'evaluations_completed': evaluations_completed,
        'dept': dept,
        'fms_total_patients': locals().get('fms_total_patients', 0),
        'fms_kadar_kawalan': locals().get('fms_kadar_kawalan', 0),
        'fms_prestasi': locals().get('fms_prestasi', ''),
        'hba1c_cusum_data': hba1c_cusum_data,
        'pending_procedures': pending_procedures,
        'recent_procedures': recent_procedures,
        'clin_cusum_drift': clin_cusum_drift,
        'doc_metrics': doc_metrics,
        'policy_review': policy_review,
        'cpd_metrics': cpd_metrics,
        'cpd_score': cpd_score,
        'cpd_trajectory': cpd_trajectory,
        'kkm_domain_choices': KKM_DOMAIN_CHOICES,
        'pending_cpd_claims': pending_cpd_claims,
        'all_cpd_claims': all_cpd_claims,
    }
    return render(request, 'dashboard/clinical_view.html', context)
# ==========================================
# MODUL OPERASIONAL
# ==========================================
@login_required(login_url='/login/')
def operational_view(request):
    if request.method == 'POST':
        form_type = (request.POST.get('form_type') or '').strip()

        if form_type == 'verify_innovation_project':
            project_id = _parse_int(request.POST.get('project_id'), 0)
            proj = InnovationProject.objects.filter(id=project_id).select_related('officer').first()
            if proj and not proj.is_verified:
                proj.is_verified = True
                proj.verified_by = request.user
                proj.verified_at = timezone.now()
                proj.save(update_fields=['is_verified', 'verified_by', 'verified_at'])
                messages.success(
                    request,
                    f"✅ Projek inovasi '{proj.project_title}' ({proj.final_points:g} pts) telah disahkan."
                )
            elif proj:
                messages.info(request, "ℹ️ Projek ini telah pun disahkan sebelum ini.")
            else:
                messages.error(request, "❌ Rekod projek inovasi tidak ditemui.")
            return redirect(request.get_full_path())

        elif form_type == 'send_hod_feedback':
            target_username = (request.POST.get('target_username') or '').strip()
            message_content = (request.POST.get('message_text') or '').strip()
            mod_ref = (request.POST.get('module_ref') or 'Operational Excellence').strip()
            severity = (request.POST.get('severity') or 'WARNING').strip()

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
                    message_text=message_content,
                )
                messages.success(request, f"✅ Mesej pemantauan berjaya dihantar kepada Dr. {target_user.first_name or target_user.username}.")
            else:
                messages.error(request, "❌ Gagal menghantar mesej. Sila pastikan pegawai dipilih dan catatan diisi.")
            return redirect(request.get_full_path())

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
    hod_facility_label = _hod_scope_label(hod_scope, hod_state, hod_facility)

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

    # --- Pembolehubah Carta & Data Sebenar Operational ---
    area_chart_html = ""
    radar_chart_html = ""
    prod_metrics = {
        'has_data': False,
        'avg_patients': 0.0,
        'ewma_latest': 0.0,
        'avg_consult': 0.0,
        'consult_status': 'Tiada Rekod',
        'consult_is_ok': True,
        'punctuality_avg': 0.0,
        'ewma_status': 'NO DATA',
        'ewma_badge_bg': '#f1f5f9',
        'ewma_badge_color': '#64748b',
        'ewma_note': 'Pegawai ini belum mempunyai log produktiviti harian.',
        'total_logs': 0,
    }
    officer_projects = []
    innov_summary = {
        'total_count': 0,
        'verified_count': 0,
        'pending_count': 0,
        'total_points': 0.0,
        'verified_points': 0.0,
        'target_points': 20.0,
        'attainment_pct': 0,
    }

    # 1. Tab Productivity: EWMA Burnout & Efficiency Surveillance
    if active_tab == 'Productivity':
        target_user = selected_staff
        prod_logs = (
            list(ProductivityLog.objects.filter(officer=target_user).order_by('-record_date', '-id')[:20])
            if target_user else []
        )
        prod_logs.reverse()

        fig_area = go.Figure()
        if prod_logs:
            tarikh_label = [log.record_date.strftime("%d %b") for log in prod_logs]
            pesakit_harian = [float(log.patients_cleared or 0) for log in prod_logs]

            # Kira siri EWMA (lambda = 0.30) untuk melicinkan turun naik harian & kesan burnout drift
            lam = 0.30
            ewma_series = []
            z_curr = pesakit_harian[0]
            for val in pesakit_harian:
                z_curr = round((lam * val) + ((1.0 - lam) * z_curr), 1)
                ewma_series.append(z_curr)

            avg_pts = round(sum(pesakit_harian) / len(pesakit_harian), 1)
            avg_cons = round(sum(float(l.avg_consult_minutes or 0) for l in prod_logs) / len(prod_logs), 1)
            avg_punc = round(sum(float(l.punctuality_rate or 0) for l in prod_logs) / len(prod_logs), 1)
            latest_ewma = ewma_series[-1]
            baseline_ewma = sum(ewma_series[:max(1, len(ewma_series) // 2)]) / max(1, len(ewma_series) // 2)

            if avg_cons > 15.0:
                c_status, c_ok = "Melebihi 15m (Risiko Keletihan)", False
            elif avg_cons < 8.0 and avg_cons > 0:
                c_status, c_ok = "Terlalu Singkat (<8m)", False
            else:
                c_status, c_ok = "Julat Optimum (10–15m)", True

            # Pengesanan Burnout / Drift melalui EWMA + Masa Konsultasi
            if (baseline_ewma > 0 and latest_ewma < baseline_ewma * 0.85) or avg_cons > 16.0 or latest_ewma < 28.0:
                e_status = "WATCH (Burnout / Velocity Drift)"
                e_bg, e_col = "#fef3c7", "#b45309"
                e_note = f"EWMA semasa ({latest_ewma} kes/hari) atau purata masa konsultasi ({avg_cons} min) menunjukkan tanda keletihan beban kerja."
            else:
                e_status = "IN CONTROL (Balanced)"
                e_bg, e_col = "#dcfce7", "#15803d"
                e_note = f"Trajektori EWMA ({latest_ewma} kes/hari) stabil dengan masa konsultasi purata {avg_cons} min/pesakit."

            prod_metrics = {
                'has_data': True,
                'avg_patients': avg_pts,
                'ewma_latest': latest_ewma,
                'avg_consult': avg_cons,
                'consult_status': c_status,
                'consult_is_ok': c_ok,
                'punctuality_avg': avg_punc,
                'ewma_status': e_status,
                'ewma_badge_bg': e_bg,
                'ewma_badge_color': e_col,
                'ewma_note': e_note,
                'total_logs': len(prod_logs),
            }

            # Plot 1: Garisan EWMA (Primary Smoothed Trajectory)
            fig_area.add_trace(go.Scatter(
                x=tarikh_label,
                y=ewma_series,
                mode='lines+markers',
                fill='tozeroy',
                line=dict(color='#6d3ec4', width=3.5, shape='spline', smoothing=0.8),
                marker=dict(size=8, color='#150a35', line=dict(color='white', width=2)),
                fillcolor='rgba(109, 62, 196, 0.12)',
                name='EWMA Velocity (λ=0.3)'
            ))
            # Plot 2: Bacaan Harian Sebenar (Raw Daily Points)
            fig_area.add_trace(go.Scatter(
                x=tarikh_label,
                y=pesakit_harian,
                mode='markers',
                marker=dict(size=7, color='#94a3b8', symbol='circle-open'),
                name='Daily Raw Patients'
            ))
            y_max = max(65, int(max(pesakit_harian) * 1.2))
        else:
            tarikh_label = ["Tiada Rekod"]
            fig_area.add_trace(go.Scatter(
                x=tarikh_label, y=[0], mode='markers',
                marker=dict(size=8, color='#94a3b8'), name='Tiada Data'
            ))
            y_max = 60

        fig_area.add_hline(
            y=38.2, line_dash="dot", line_color="#6d3ec4",
            annotation_text="MoH Benchmark (38.2/day)"
        )
        fig_area.update_layout(
            height=300,
            margin=dict(l=10, r=15, t=25, b=10),
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            font=dict(color="#150a35"),
            yaxis=dict(range=[0, y_max], gridcolor='#e7dff2', title="Patients Cleared"),
            xaxis=dict(showgrid=False, title="Record Date"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(size=10))
        )
        area_chart_html = fig_area.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False})

    # 2. Tab Innovation: Portfolio Wajaran & Pengesahan HOD
    elif active_tab == 'Innovation':
        target_user = selected_staff
        officer_projects = (
            list(InnovationProject.objects.filter(officer=target_user).order_by('-submitted_at', '-id'))
            if target_user else []
        )

        kategori = ['Research', 'Clinical Trial', 'Technology', 'Publication']
        points_by_type = {k: 0.0 for k in kategori}

        for p in officer_projects:
            pts = float(p.final_points or p.score or 0.0)
            itype = (p.innovation_type or '').strip()
            if itype in points_by_type:
                points_by_type[itype] += pts
            else:
                points_by_type['Technology'] += pts

        total_pts = round(sum(float(p.final_points or 0.0) for p in officer_projects), 1)
        verified_pts = round(sum(float(p.final_points or 0.0) for p in officer_projects if p.is_verified), 1)
        verified_cnt = sum(1 for p in officer_projects if p.is_verified)
        pending_cnt = len(officer_projects) - verified_cnt

        innov_summary = {
            'total_count': len(officer_projects),
            'verified_count': verified_cnt,
            'pending_count': pending_cnt,
            'total_points': total_pts,
            'verified_points': verified_pts,
            'target_points': 20.0,
            'attainment_pct': min(100, int(round((verified_pts / 20.0) * 100))),
        }

        markah = [round(points_by_type[k], 1) for k in kategori]
        x_max = max(25.0, max(markah) * 1.25)

        fig_pop = go.Figure()
        for i in range(len(kategori)):
            fig_pop.add_shape(
                type='line',
                x0=0, y0=kategori[i], x1=markah[i], y1=kategori[i],
                line=dict(color='#d8c7f4', width=5)
            )

        fig_pop.add_trace(go.Scatter(
            x=markah,
            y=kategori,
            mode='markers+text',
            marker=dict(color='#6d3ec4', size=16, line=dict(color='white', width=2)),
            text=[f"{m:g} pts" if m > 0 else "0 pts" for m in markah],
            textposition='middle right',
            textfont=dict(color='#150a35', size=12, weight='bold'),
            hoverinfo='none'
        ))
        fig_pop.add_vline(
            x=20, line_dash="dot", line_color="#16a34a",
            annotation_text="Annual Target (20 pts)"
        )
        fig_pop.update_layout(
            height=290, margin=dict(t=25, b=25, l=10, r=45),
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            font=dict(color="#150a35"), showlegend=False,
            xaxis=dict(range=[0, x_max], showgrid=True, gridcolor='#e7dff2', title=dict(text='Weighted Points (Base × Multiplier)', font=dict(color='#150a35'))),
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
        'pilihan_doktor_username': (
            selected_staff.username if selected_staff else ''
        ),
        'area_chart_html': area_chart_html,
        'radar_chart_html': radar_chart_html,
        'prod_metrics': prod_metrics,
        'officer_projects': officer_projects,
        'innov_summary': innov_summary,
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
    hod_facility_label = _hod_scope_label(hod_scope, hod_state, hod_facility)

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
        'pilihan_doktor_username': (
            selected_staff.username if selected_staff else ''
        ),
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
    'Severe Mental Illness',
    'Palliative Care',
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
        # Medan renal/komplikasi hanya diisi oleh borang FMS.
        serum_creatinine = None
        egfr = None
        ckd_stage = None
        urine_microalbumin = None
        visual_acuity = None
        fundus_result = None
        fundus_date = None
        foot_exam_risk = None
        foot_exam_date = None
        ecg_result = None
        ecg_date = None
        cxr_result = None
        cxr_date = None
        fatty_liver = None
        fatty_liver_date = None
        ed_screening = None
        dietitian_referral_date = None
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

        # Renal Profile & Complication Screening Data
        serum_creatinine = _optional_float(request.POST.get('serum_creatinine'), 'Serum Creatinine')
        egfr = _optional_float(request.POST.get('egfr'), 'eGFR')
        ckd_stage = _optional_text(request.POST.get('ckd_stage'), max_length=30)
        urine_microalbumin = _optional_text(request.POST.get('urine_microalbumin'), max_length=30)

        visual_acuity = _optional_text(request.POST.get('visual_acuity'), max_length=50)
        fundus_result = _optional_text(request.POST.get('fundus_result'), max_length=50)
        fundus_date = _optional_date(request.POST.get('fundus_date'), 'Fundus Date')
        foot_exam_risk = _optional_text(request.POST.get('foot_exam_risk'), max_length=50)
        foot_exam_date = _optional_date(request.POST.get('foot_exam_date'), 'Foot Exam Date')
        ecg_result = _optional_text(request.POST.get('ecg_result'), max_length=50)
        ecg_date = _optional_date(request.POST.get('ecg_date'), 'ECG Date')
        cxr_result = _optional_text(request.POST.get('cxr_result'), max_length=50)
        cxr_date = _optional_date(request.POST.get('cxr_date'), 'CXR Date')
        fatty_liver = _optional_text(request.POST.get('fatty_liver'), max_length=30)
        fatty_liver_date = _optional_date(request.POST.get('fatty_liver_date'), 'Fatty Liver Date')
        ed_screening = _optional_text(request.POST.get('ed_screening'), max_length=30)
        dietitian_referral_date = _optional_date(request.POST.get('dietitian_referral_date'), 'Dietitian Referral Date')

        target_raw = (request.POST.get('target') or '').strip()
        if target_raw not in ('Yes', 'No'):
            raise ValidationError('Sila pilih status pencapaian target.')
        target_achieved = (target_raw == 'Yes')
        primary_diagnosis = _optional_text(request.POST.get('dm_type'), max_length=200)
        previous_intervention = None
        lvef_percent = None
        coronary_anatomy = None
        nyha_class = None

    raw_nric = (request.POST.get('patient_nric') or '').strip()
    raw_mrn = (request.POST.get('patient_id') or '').strip()
    hashed_ref = _hash_patient_ref(raw_nric or raw_mrn) if (raw_nric or raw_mrn) else None

    with transaction.atomic():
        proc_obj = ProcedureLog.objects.create(
            officer=request.user,
            patient_ref=hashed_ref,
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
            serum_creatinine=serum_creatinine,
            egfr=egfr,
            ckd_stage=ckd_stage,
            urine_microalbumin=urine_microalbumin,
            visual_acuity=visual_acuity,
            fundus_result=fundus_result,
            fundus_date=fundus_date,
            foot_exam_risk=foot_exam_risk,
            foot_exam_date=foot_exam_date,
            ecg_result=ecg_result,
            ecg_date=ecg_date,
            cxr_result=cxr_result,
            cxr_date=cxr_date,
            fatty_liver=fatty_liver,
            fatty_liver_date=fatty_liver_date,
            ed_screening=ed_screening,
            dietitian_referral_date=dietitian_referral_date,
            is_successful=True,
            complication_occurred=False,
            target_achieved=target_achieved,
            score=score_val,
        )

        # PENJANAAN TIKET DOKUMENTASI AUTOMATIK (SLA 4 JAM BERMULA)
        pt_mrn = (request.POST.get('patient_id') or '').strip()
        pt_name = (request.POST.get('patient_name') or '').strip()
        if pt_mrn and pt_name:
            patient_display = f"{pt_mrn} ({pt_name})"
        elif pt_mrn:
            patient_display = pt_mrn
        else:
            patient_display = f"CASE-{proc_obj.id:04d}"

        DocumentationLog.objects.create(
            officer=request.user,
            procedure=proc_obj,
            patient_id=patient_display[:100],
            encounter_type=f"{proc_name} [{clinical_domain}]",
            encounter_at=timezone.now(),
            completed_at=None,
            delay_hours=0.0,
            is_completed=False,
            is_ontime=True
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

    # Tangkap parameter Model Pemarkahan Inovasi Baharu (8 Aktiviti & Pengganda 5, 1, 2, 0)
    activity_type = _optional_text(request.POST.get('activity_type'), max_length=100)
    base_points = _optional_float(request.POST.get('base_points'), 'Base Points') or 0.0
    contribution_level = _optional_text(request.POST.get('contribution_level'), max_length=100) or 'Lead contribution'

    # Pemetaan Pengganda Mengikut Penetapan KKM Baharu (5, 1, 2, 0)
    multiplier_map = {
        'Lead contribution': 5.0,
        'Substantial contribution': 1.0,
        'Supporting contribution': 2.0,
        'Minimal or unverified contribution': 0.0,
    }
    multiplier = multiplier_map.get(contribution_level, 0.0)
    final_points = round(base_points * multiplier, 1)

    if activity_type:
        description_lines.append(f'Activity: {activity_type}')
    description_lines.append(f'Contribution: {contribution_level} (Multiplier x{multiplier:g})')
    description_lines.append(f'Total Score: {final_points} Pts')

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
            activity_type=activity_type,
            base_points=base_points,
            contribution_level=contribution_level,
            multiplier=multiplier,
            final_points=final_points,
            score=final_points,
        )


CLINICAL_CPD_TARGET_POINTS = 50
CLINICAL_DOC_ONTIME_TARGET_PCT = 80
OPERATIONAL_RECENT_LOG_LIMIT = 14
OPERATIONAL_INNOVATION_GROUPS = (
    'Research',
    'Clinical Trial',
    'Technology Adoption',
    'Publication',
)
OPERATIONAL_LEGACY_INNOVATION_LABEL = 'General/Uncategorised'
OPERATIONAL_TECH_PROFICIENCY = {
    'Amateur (Learning)': {'progress': 33, 'color': '#a577ff'},
    'Intermediate (Independent)': {'progress': 66, 'color': '#6f628b'},
    'Expert (Trainer)': {'progress': 100, 'color': '#6d3ec4'},
}


def _format_hours_minutes(hours):
    """Format decimal hours for the clinical documentation summary."""
    if hours is None:
        return None
    total_minutes = max(0, round(float(hours) * 60))
    return f"{total_minutes // 60}h {total_minutes % 60:02d}m"


def _build_patients_context(user, raw_query=''):
    """Bina direktori pesakit, graf trend HbA1c & timeline rawatan berasaskan MRN / Hash NRIC."""
    query = (raw_query or '').strip()
    hashed_query = _hash_patient_ref(query) if query else ''

    procs = list(
        ProcedureLog.objects.filter(officer=user)
        .prefetch_related('doc_logs')
        .order_by('procedure_date', 'id')
    )

    grouped = {}
    for p in procs:
        linked_docs = list(p.doc_logs.all())
        doc_label = linked_docs[0].patient_id if linked_docs else ''
        key = p.patient_ref or doc_label or f"CASE-{p.id:04d}"

        if key not in grouped:
            display_label = doc_label or key
            search_token = doc_label.split(' (')[0].strip() if doc_label else key
            grouped[key] = {
                'key': key,
                'display_label': display_label,
                'search_token': search_token,
                'ref_badge': p.patient_ref or _hash_patient_ref(search_token),
                'visits': [],
            }
        elif doc_label and grouped[key]['display_label'].startswith('REF-'):
            grouped[key]['display_label'] = doc_label
            grouped[key]['search_token'] = doc_label.split(' (')[0].strip()

        grouped[key]['visits'].append(p)

    all_patients = list(grouped.values())
    all_patients.sort(
        key=lambda item: (item['visits'][-1].procedure_date, item['visits'][-1].id),
        reverse=True,
    )

    matched_patients = all_patients
    if query:
        q_lower = query.lower()
        matched_patients = [
            pt for pt in all_patients
            if (
                q_lower in pt['display_label'].lower()
                or q_lower in pt['key'].lower()
                or pt['ref_badge'] == hashed_query
                or pt['key'] == hashed_query
            )
        ]

    selected_patient = None
    hba1c_chart_html = ''

    if matched_patients:
        chosen = matched_patients[0]
        visits = chosen['visits']
        latest = visits[-1]

        # Siri data HbA1c untuk graf garis
        hba1c_visits = [v for v in visits if v.hba1c_percent is not None]
        if hba1c_visits:
            latest_hba1c = float(hba1c_visits[-1].hba1c_percent)
            latest_hba1c_display = f"{latest_hba1c:.1f}%"
            if latest_hba1c < 7.0:
                hba1c_color = '#16a34a'
                hba1c_status = '✓ Optimal Control (< 7.0%)'
            elif latest_hba1c < 8.5:
                hba1c_color = '#d97706'
                hba1c_status = '⚠️ Sub-Optimal (7.0–8.4%)'
            else:
                hba1c_color = '#dc2626'
                hba1c_status = '🚨 Uncontrolled (≥ 8.5%)'

            x_dates = [v.procedure_date.strftime('%d %b %Y') for v in hba1c_visits]
            y_vals = [float(v.hba1c_percent) for v in hba1c_visits]

            fig_hba1c = go.Figure()
            fig_hba1c.add_hline(
                y=7.0, line_dash='dash', line_color='#16a34a',
                annotation_text='CPG Target (< 7.0%)'
            )
            fig_hba1c.add_hline(
                y=8.5, line_dash='dot', line_color='#dc2626',
                annotation_text='High Risk (8.5%)'
            )
            fig_hba1c.add_trace(go.Scatter(
                x=x_dates,
                y=y_vals,
                mode='lines+markers+text',
                text=[f"{val:.1f}%" for val in y_vals],
                textposition='top center',
                textfont=dict(color='#150a35', size=11, weight='bold'),
                line=dict(color='#6d3ec4', width=3.5, shape='spline'),
                marker=dict(size=10, color='#150a35', line=dict(color='white', width=2)),
                name='HbA1c (%)'
            ))
            y_upper = max(11.0, max(y_vals) + 1.5)
            fig_hba1c.update_layout(
                height=290,
                margin=dict(t=25, b=20, l=35, r=25),
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(0,0,0,0)',
                yaxis=dict(range=[4.0, y_upper], gridcolor='#e7dff2', title='HbA1c (%)'),
                xaxis=dict(showgrid=False, title='Tarikh Lawatan'),
                showlegend=False,
            )
            hba1c_chart_html = fig_hba1c.to_html(
                full_html=False, include_plotlyjs=False, config={'displayModeBar': False}
            )
        else:
            latest_hba1c_display = 'Tiada Rekod'
            hba1c_color = '#6d3ec4'
            hba1c_status = 'Belum direkodkan'

        # Bacaan terkini BP, Renal/CKD & LDL
        bp_visits = [v for v in visits if v.systolic_bp and v.diastolic_bp]
        latest_bp_display = (
            f"{bp_visits[-1].systolic_bp}/{bp_visits[-1].diastolic_bp} mmHg"
            if bp_visits else 'Tiada Rekod'
        )

        ckd_visits = [v for v in visits if v.ckd_stage or v.egfr]
        if ckd_visits:
            cv = ckd_visits[-1]
            latest_ckd_display = cv.ckd_stage or 'eGFR Recorded'
            latest_egfr_display = f"eGFR: {cv.egfr:g} mL/min" if cv.egfr else 'Renal profile logged'
        else:
            latest_ckd_display = 'Tiada Rekod'
            latest_egfr_display = 'Belum direkodkan'

        ldl_visits = [v for v in visits if v.ldl_mmol_l is not None]
        latest_ldl_display = f"{ldl_visits[-1].ldl_mmol_l:.1f} mmol/L" if ldl_visits else 'Tiada Rekod'

        # Timeline kronologi (terkini di atas)
        timeline = []
        for v in reversed(visits):
            scr_parts = []
            if v.fundus_result:
                scr_parts.append(f"Fundus: {v.fundus_result}")
            if v.foot_exam_risk:
                scr_parts.append(f"Foot: {v.foot_exam_risk}")
            if v.ecg_result:
                scr_parts.append(f"ECG: {v.ecg_result}")
            if v.dietitian_referral_date:
                scr_parts.append(f"Dietitian Ref: {v.dietitian_referral_date.strftime('%d/%m/%Y')}")

            renal_parts = []
            if v.egfr is not None:
                renal_parts.append(f"eGFR {v.egfr:g}")
            if v.ckd_stage:
                renal_parts.append(v.ckd_stage)

            timeline.append({
                'date_display': v.procedure_date.strftime('%d %b %Y'),
                'condition': v.procedure_name,
                'target_achieved': v.target_achieved,
                'hba1c_display': f"{v.hba1c_percent:.1f}%" if v.hba1c_percent is not None else '—',
                'bp_display': f"{v.systolic_bp}/{v.diastolic_bp} mmHg" if (v.systolic_bp and v.diastolic_bp) else '—',
                'ldl_display': f"{v.ldl_mmol_l:.1f} mmol/L" if v.ldl_mmol_l is not None else '—',
                'renal_display': ' • '.join(renal_parts) if renal_parts else '—',
                'screenings': ' | '.join(scr_parts),
                'comorbidities': ', '.join(v.comorbidities) if isinstance(v.comorbidities, list) and v.comorbidities else '',
            })

        selected_patient = {
            'key': chosen['key'],
            'display_label': chosen['display_label'],
            'ref_badge': chosen['ref_badge'],
            'primary_condition': latest.procedure_name,
            'visit_count': len(visits),
            'has_hba1c_points': bool(hba1c_visits),
            'latest_hba1c_display': latest_hba1c_display,
            'hba1c_color': hba1c_color,
            'hba1c_status': hba1c_status,
            'latest_bp_display': latest_bp_display,
            'latest_ckd_display': latest_ckd_display,
            'latest_egfr_display': latest_egfr_display,
            'latest_ldl_display': latest_ldl_display,
            'timeline': timeline,
        }

    return {
        'patient_search_query': query,
        'patient_directory': all_patients[:12],
        'patient_total_count': len(all_patients),
        'selected_patient': selected_patient,
        'hba1c_chart_html': hba1c_chart_html,
    }


def _build_clinical_context(user, doc_logs):
    """Build CPD and documentation values for the staff Clinical tab."""
    current_year = timezone.localdate().year
    claims = list(
        CPDClaim.objects.filter(officer=user, event_date__year=current_year)
        .order_by('-submitted_at', '-event_date', '-id')
    )
    cpd_trajectory = build_cpd_trajectory(user)
    approved_points = cpd_trajectory['points']['approved']
    pending_claims = [
        claim for claim in claims
        if claim.status == 'PENDING' and claim.kkm_domain in KKM_CORE_DOMAINS
    ]
    pending_points = cpd_trajectory['points']['pending']
    cpd_points_pct = cpd_trajectory['points']['pct']
    cpd_coverage_pct = cpd_trajectory['coverage']['weighted_pct']
    cpd_covered_count = cpd_trajectory['coverage']['covered_count']
    cpd_core_total = cpd_trajectory['coverage']['core_total']
    cpd_missing_domains = cpd_trajectory['coverage']['missing']
    cpd_rare_missing = cpd_trajectory['coverage']['rare_missing']
    cpd_legacy_count = cpd_trajectory['legacy_ignored_count']

    now_ts = timezone.now()
    
    # 1. Asingkan Giliran Menunggu (Pending) dan Rekod Selesai
    pending_list = []
    for d in doc_logs.filter(is_completed=False).order_by('-encounter_at'):
        start_t = d.encounter_at or d.created_at
        elapsed = round((now_ts - start_t).total_seconds() / 3600.0, 2)
        remaining = round(4.0 - elapsed, 2)
        is_overdue = (elapsed > 4.0)
        
        # Jam Randuk Status Warna (English Standard)
        if is_overdue:
            status_color = '#dc2626' # Red (Overdue)
            status_label = f"OVERDUE ({elapsed:.1f}h)"
        elif remaining <= 1.0:
            status_color = '#d97706' # Amber (Critical < 1 Hour)
            status_label = f"{int(remaining * 60)} Mins Left"
        else:
            status_color = '#16a34a' # Green (On-Time)
            status_label = f"{remaining:.1f}h Remaining"

        pending_list.append({
            'doc': d,
            'elapsed_hours': elapsed,
            'remaining_hours': remaining,
            'is_overdue': is_overdue,
            'status_color': status_color,
            'status_label': status_label,
            'started_time_display': timezone.localtime(start_t).strftime('%I:%M %p, %d %b %Y')
        })

    completed_docs = doc_logs.filter(is_completed=True)
    total_completed = completed_docs.count()
    ontime_completed = completed_docs.filter(is_ontime=True).count() if total_completed else 0
    average_delay = (
        completed_docs.aggregate(average_delay=Avg('delay_hours'))['average_delay']
        if total_completed else None
    )
    ontime_rate = (
        round((ontime_completed / total_completed) * 100) if total_completed else None
    )

    if not cpd_trajectory['has_eligible_claims']:
        status_label = 'Not Started'
        status_color = '#6d3ec4'
        status_background = '#f3edff'
        status_border = '#e7dff2'
    elif not approved_points and pending_points:
        status_label = 'Pending Verification'
        status_color = '#D97706'
        status_background = '#FFFBEB'
        status_border = '#FEF08A'
    elif (
        approved_points >= CLINICAL_CPD_TARGET_POINTS
        and cpd_covered_count >= cpd_core_total
    ):
        status_label = 'Completed'
        status_color = '#16A34A'
        status_background = '#F0FDF4'
        status_border = '#BBF7D0'
    elif approved_points >= CLINICAL_CPD_TARGET_POINTS:
        status_label = 'Coverage Gap'
        status_color = '#D97706'
        status_background = '#FFFBEB'
        status_border = '#FEF08A'
    else:
        status_label = 'Behind Target'
        status_color = '#D97706'
        status_background = '#FFFBEB'
        status_border = '#FEF08A'

    proc_qs = ProcedureLog.objects.filter(officer=user).order_by('-procedure_date', '-id')
    procedure_logs = list(proc_qs[:30])
    procedure_log_count = proc_qs.count()

    return {
        'procedure_logs': procedure_logs,
        'procedure_log_count': procedure_log_count,
        'cpd_year': current_year,
        'cpd_claims': claims[:20],
        'cpd_claim_count': len(claims),
        'cpd_approved_points': approved_points,
        'cpd_pending_points': pending_points,
        'cpd_pending_count': len(pending_claims),
        'cpd_target_points': CLINICAL_CPD_TARGET_POINTS,
        'cpd_score': cpd_trajectory['score']['final'],
        'cpd_points_pct': cpd_points_pct,
        'cpd_coverage_pct': cpd_coverage_pct,
        'cpd_covered_count': cpd_covered_count,
        'cpd_core_total': cpd_core_total,
        'cpd_missing_domains': cpd_missing_domains,
        'cpd_rare_missing': cpd_rare_missing,
        'cpd_legacy_count': cpd_legacy_count,
        'cpd_trajectory': cpd_trajectory,
        'cpd_on_track': (
            cpd_trajectory['has_eligible_claims']
            and approved_points >= CLINICAL_CPD_TARGET_POINTS
            and cpd_covered_count >= cpd_core_total
        ),
        'kkm_domain_choices': KKM_DOMAIN_CHOICES,
        'cpd_status_label': status_label,
        'cpd_status_color': status_color,
        'cpd_status_background': status_background,
        'cpd_status_border': status_border,
        'pending_docs': pending_list,
        'pending_docs_count': len(pending_list),
        'completed_doc_logs': completed_docs.order_by('-completed_at')[:20],
        'doc_total': total_completed,
        'doc_ontime_rate': ontime_rate,
        'doc_ontime_rate_display': (
            f'{ontime_rate}%' if ontime_rate is not None else 'Tiada rekod'
        ),
        'doc_avg_delay_display': _format_hours_minutes(average_delay) or '0h 00m',
        'doc_ontime_target_pct': CLINICAL_DOC_ONTIME_TARGET_PCT,
    }


def _operational_metric_display(value, decimal_places=1, suffix=''):
    """Format an optional dashboard metric without turning missing data into 0."""
    if value is None:
        return 'No data'
    if decimal_places == 0:
        text = str(int(round(value)))
    else:
        text = f'{round(value, decimal_places):.{decimal_places}f}'.rstrip('0').rstrip('.')
    return f'{text}{suffix}'


def _operational_average(values):
    """Average only recorded values so blank fields are not counted as zero."""
    recorded = [value for value in values if value is not None]
    return sum(recorded) / len(recorded) if recorded else None


def _operational_staff_name(user):
    """Return the name used for the department leaderboard."""
    full_name = user.get_full_name().strip()
    return full_name or user.username


def _classify_innovation_project(project):
    """Map a project to one of the four operational innovation groups."""
    innovation_type = (project.innovation_type or '').strip()
    if innovation_type in OPERATIONAL_INNOVATION_GROUPS:
        return innovation_type

    category = (project.category or '').strip()
    # Older records used "Technology Adaptation" while the current form label
    # and stored innovation_type are "Technology Adoption".
    if category == 'Technology Adaptation':
        return 'Technology Adoption'
    if category in OPERATIONAL_INNOVATION_GROUPS:
        return category
    return OPERATIONAL_LEGACY_INNOVATION_LABEL


def _operational_project_payload(project, innovation_type):
    """Build the template-facing representation of one innovation project."""
    is_verified = bool(project.is_verified)
    payload = {
        'id': project.id,
        'title': project.project_title,
        'submitted_date_display': (
            project.submitted_at.strftime('%d %b %Y')
            if project.submitted_at else 'Not recorded'
        ),
        'score': project.score,
        'activity_type': getattr(project, 'activity_type', None) or 'General',
        'base_points': getattr(project, 'base_points', 0.0),
        'contribution_level': getattr(project, 'contribution_level', None) or 'Lead contribution',
        'multiplier': getattr(project, 'multiplier', 1.0),
        'final_points': getattr(project, 'final_points', project.score),
        'is_verified': is_verified,
        'status_label': 'Verified' if is_verified else 'Pending Verification',
        'status_css': 'bg-success' if is_verified else 'bg-warning',
    }

    if innovation_type == 'Research':
        payload.update({
            'nmrr_id': project.nmrr_id or 'Not recorded',
            'research_role': project.research_role or 'Not recorded',
        })
    elif innovation_type == 'Clinical Trial':
        payload.update({
            'trial_phase': project.trial_phase or 'Not recorded',
            'recruitment_display': (
                f'{project.recruitment_count} Patients'
                if project.recruitment_count is not None else 'Not recorded'
            ),
            'trial_status': project.trial_status or 'Not recorded',
        })
    elif innovation_type == 'Technology Adoption':
        proficiency = (project.proficiency_level or '').strip()
        proficiency_style = OPERATIONAL_TECH_PROFICIENCY.get(
            proficiency, {'progress': 0, 'color': '#94a3b8'}
        )
        payload.update({
            'adoption_date_display': (
                project.adoption_date.strftime('%d %b %Y')
                if project.adoption_date else 'Not recorded'
            ),
            'proficiency_display': proficiency or 'Not recorded',
            'proficiency_progress': proficiency_style['progress'],
            'proficiency_color': proficiency_style['color'],
        })
    elif innovation_type == 'Publication':
        payload.update({
            'journal': project.journal_name or 'Not recorded',
            'doi': project.doi or 'Not recorded',
            'publication_year_display': (
                project.publication_year
                if project.publication_year is not None else 'Not recorded'
            ),
        })
    return payload


def _build_department_leaderboard(user):
    """Rank same-department staff by current-year innovation output."""
    current_year = timezone.localdate().year
    empty_result = {
        'ops_department': None,
        'ops_dept_rank': None,
        'ops_dept_rank_display': 'No Rank',
        'ops_dept_rank_total': 0,
        'ops_leaderboard': [],
    }

    profile = getattr(user, 'profile', None)
    department = getattr(profile, 'department', None)
    if not department:
        return empty_result

    profiles = list(
        UserProfile.objects.filter(role='STAFF', department=department)
        .select_related('user')
    )
    if not profiles:
        return empty_result

    officer_ids = [staff_profile.user_id for staff_profile in profiles]
    projects = InnovationProject.objects.filter(
        officer_id__in=officer_ids,
        submitted_at__year=current_year,
    ).only(
        'id', 'officer_id', 'innovation_type', 'category', 'is_verified'
    )
    projects_by_officer = {officer_id: [] for officer_id in officer_ids}
    for project in projects:
        projects_by_officer.setdefault(project.officer_id, []).append(project)

    rows = []
    for staff_profile in profiles:
        staff_projects = projects_by_officer.get(staff_profile.user_id, [])
        display_name = _operational_staff_name(staff_profile.user)
        rows.append({
            'user_id': staff_profile.user_id,
            'name': display_name,
            'project_count': len(staff_projects),
            'verified_count': sum(
                1 for project in staff_projects if project.is_verified
            ),
            'publication_count': sum(
                1 for project in staff_projects
                if _classify_innovation_project(project) == 'Publication'
            ),
            'is_current_user': staff_profile.user_id == user.id,
        })

    rows.sort(key=lambda row: (
        -row['project_count'],
        -row['verified_count'],
        -row['publication_count'],
        row['name'].casefold(),
        row['user_id'],
    ))

    current_row = None
    for rank, row in enumerate(rows, start=1):
        row['rank'] = rank
        if row['is_current_user']:
            current_row = row

    leaderboard_rows = rows[:3]
    if current_row and current_row['rank'] > len(leaderboard_rows):
        leaderboard_rows.append(current_row)

    return {
        'ops_department': department,
        'ops_dept_rank': current_row['rank'] if current_row else None,
        'ops_dept_rank_display': (
            f"#{current_row['rank']} of {len(rows)}"
            if current_row else 'No Rank'
        ),
        'ops_dept_rank_total': len(rows),
        'ops_leaderboard': leaderboard_rows,
    }


def _build_operational_context(user, prod_logs):
    """Build productivity and innovation values for the staff Operational tab."""
    current_year = timezone.localdate().year
    logs = list(prod_logs)

    cleared_avg = _operational_average(
        [log.patients_cleared for log in logs]
    )
    ward_avg = _operational_average([log.ward_cases for log in logs])
    procedure_avg = _operational_average(
        [log.procedures_completed for log in logs]
    )
    consult_avg = _operational_average(
        [log.avg_consult_minutes for log in logs]
    )
    punctuality_avg = _operational_average(
        [log.punctuality_rate for log in logs]
    )

    recent_logs = []
    for log in logs[:OPERATIONAL_RECENT_LOG_LIMIT]:
        recent_logs.append({
            'record_date_display': log.record_date.strftime('%d %b %Y'),
            'patients_cleared_display': (
                f'{log.patients_cleared} Cases'
                if log.patients_cleared is not None else 'Not recorded'
            ),
            'ward_cases_display': (
                f'{log.ward_cases} Cases'
                if log.ward_cases is not None else 'Not recorded'
            ),
            'procedures_display': (
                f'{log.procedures_completed} Cases'
                if log.procedures_completed is not None else 'Not recorded'
            ),
            'avg_consult_display': (
                f'{_operational_metric_display(log.avg_consult_minutes)} Mins'
                if log.avg_consult_minutes is not None else 'Not recorded'
            ),
            'punctuality_display': log.punctuality_status or 'Not recorded',
            'status_label': 'Recorded',
        })

    projects = list(
        InnovationProject.objects.filter(officer=user)
        .order_by('-submitted_at', '-id')
    )
    grouped_projects = {
        group: [] for group in OPERATIONAL_INNOVATION_GROUPS
    }
    grouped_projects[OPERATIONAL_LEGACY_INNOVATION_LABEL] = []
    for project in projects:
        innovation_type = _classify_innovation_project(project)
        grouped_projects[innovation_type].append(
            _operational_project_payload(project, innovation_type)
        )

    verified_count = sum(1 for project in projects if project.is_verified)
    average_score = _operational_average(
        [project.score for project in projects]
    )

    return {
        'ops_current_year': current_year,
        'ops_productivity_log_count': len(logs),
        'prod_cleared_avg': cleared_avg,
        'prod_cleared_avg_display': _operational_metric_display(cleared_avg),
        'prod_ward_avg': ward_avg,
        'prod_ward_avg_display': _operational_metric_display(ward_avg),
        'prod_procedure_avg': procedure_avg,
        'prod_procedure_avg_display': _operational_metric_display(
            procedure_avg
        ),
        'prod_consult_avg': consult_avg,
        'prod_consult_avg_display': _operational_metric_display(consult_avg),
        'prod_punctuality_avg': punctuality_avg,
        'prod_punctuality_avg_display': _operational_metric_display(
            punctuality_avg, decimal_places=0, suffix='%'
        ),
        'prod_has_logs': bool(logs),
        'prod_has_consult_avg': consult_avg is not None,
        'prod_has_punctuality_avg': punctuality_avg is not None,
        'prod_recent_logs': recent_logs,
        'ops_total_projects': len(projects),
        'ops_verified_projects': verified_count,
        'ops_pending_projects': len(projects) - verified_count,
        'ops_publication_count': len(grouped_projects['Publication']),
        'ops_uncategorised_count': len(
            grouped_projects[OPERATIONAL_LEGACY_INNOVATION_LABEL]
        ),
        'ops_average_score': average_score,
        'ops_average_score_display': _operational_metric_display(
            average_score
        ),
        'ops_average_score_is_provisional': (
            bool(projects) and verified_count == 0
        ),
        'ops_research_projects': grouped_projects['Research'],
        'ops_trial_projects': grouped_projects['Clinical Trial'],
        'ops_technology_projects': grouped_projects['Technology Adoption'],
        'ops_publication_projects': grouped_projects['Publication'],
        'ops_uncategorised_projects': grouped_projects[
            OPERATIONAL_LEGACY_INNOVATION_LABEL
        ],
        **_build_department_leaderboard(user),
    }


INTERPERSONAL_EI_HISTORY_LIMIT = 10
INTERPERSONAL_KUDOS_FEED_LIMIT = 20
INTERPERSONAL_MENTORSHIP_HISTORY_LIMIT = 20


def _interpersonal_display_name(user):
    """Return a consistent doctor name for Interpersonal activity rows."""
    if user is None:
        return 'Unknown user'
    full_name = user.get_full_name().strip()
    return f"Dr. {full_name or user.username}"


def _interpersonal_normalise_name(value):
    """Normalise free-text mentee names before counting unique mentees."""
    return re.sub(r'\s+', ' ', (value or '').strip()).casefold()


def _interpersonal_week_streak(reflections, today):
    """Count consecutive ISO weeks with a reflection and one-week grace."""
    week_starts = {
        reflection.record_date - timedelta(days=reflection.record_date.weekday())
        for reflection in reflections
    }
    current_week_start = today - timedelta(days=today.weekday())
    if current_week_start in week_starts:
        cursor = current_week_start
    elif current_week_start - timedelta(days=7) in week_starts:
        cursor = current_week_start - timedelta(days=7)
    else:
        return 0

    streak = 0
    while cursor in week_starts:
        streak += 1
        cursor -= timedelta(days=7)
    return streak


def _interpersonal_duration_display(hours):
    """Format a mentorship duration without trailing decimal zeroes."""
    value = float(hours or 0)
    text = f'{value:.1f}'.rstrip('0').rstrip('.')
    unit = 'Hr' if value == 1 else 'Hrs'
    return f'{text} {unit}'


def _build_interpersonal_context(user, qr_eval_url):
    """Build DB-backed values for the staff Interpersonal tab."""
    today = timezone.localdate()
    current_year = today.year

    # --- MyPSQ: real patient feedback only; never invent a fallback rating ---
    feedbacks = list(
        MyPSQFeedback.objects.filter(doctor=user)
        .order_by('-created_at', '-id')
    )
    mypsq_count = len(feedbacks)
    if mypsq_count:
        average_rating = (
            sum(feedback.average_score for feedback in feedbacks)
            / mypsq_count
        )
        mypsq_avg_score = round(average_rating, 1)
        mypsq_pct = int((average_rating / 5.0) * 100)
        mypsq_latest_date_display = timezone.localtime(
            feedbacks[0].created_at
        ).strftime('%d %b %Y')
    else:
        mypsq_avg_score = None
        mypsq_pct = None
        mypsq_latest_date_display = 'No data'

    # --- Emotional Intelligence: all-time domain averages and weekly streak ---
    reflections = list(
        EmotionalReflection.objects.filter(officer=user)
        .order_by('-record_date', '-id')
    )
    domain_averages = _average_reflection_domains(reflections)
    ei_domain_cards = []
    for index, (_post_key, field_name, label) in enumerate(EI_DOMAIN_FIELDS):
        recorded_values = [
            getattr(reflection, field_name)
            for reflection in reflections
            if getattr(reflection, field_name) is not None
        ]
        average = domain_averages[index]
        ei_domain_cards.append({
            'key': field_name,
            'label': label,
            'average': average,
            'display': f'{average}%' if average is not None else 'No data',
            'bar_width': average or 0,
            'recorded_count': len(recorded_values),
            'has_data': average is not None,
        })

    recorded_domain_averages = [
        average for average in domain_averages if average is not None
    ]
    ei_domain_count = len(recorded_domain_averages)
    ei_composite = (
        round(sum(recorded_domain_averages) / ei_domain_count)
        if ei_domain_count else None
    )
    ei_composite_is_provisional = 0 < ei_domain_count < len(EI_DOMAIN_FIELDS)
    if ei_composite is None:
        ei_composite_status_display = 'No data'
    elif ei_composite_is_provisional:
        ei_composite_status_display = (
            f'Provisional ({ei_domain_count}/{len(EI_DOMAIN_FIELDS)} domains)'
        )
    else:
        ei_composite_status_display = (
            f'Complete ({ei_domain_count}/{len(EI_DOMAIN_FIELDS)} domains)'
        )

    ei_reflections = []
    for reflection in reflections[:INTERPERSONAL_EI_HISTORY_LIMIT]:
        ei_reflections.append({
            'id': reflection.id,
            'record_date_display': reflection.record_date.strftime('%d %b %Y'),
            'trigger_display': (
                reflection.trigger_event or 'Trigger not recorded'
            ),
            'coping_display': (
                reflection.coping_strategy or 'Coping strategy not recorded'
            ),
            'notes_display': (
                reflection.reflection_notes or 'No reflection notes recorded.'
            ),
            'scores': [
                {
                    'label': label,
                    'display': (
                        f'{getattr(reflection, field_name)}%'
                        if getattr(reflection, field_name) is not None
                        else 'Not recorded'
                    ),
                }
                for _post_key, field_name, label in EI_DOMAIN_FIELDS
            ],
        })

    ei_streak_weeks = _interpersonal_week_streak(reflections, today)

    # --- Peer Kudos: a personal, privacy-preserving two-way activity feed ---
    kudos_records = list(
        PeerKudos.objects.filter(Q(sender=user) | Q(receiver=user))
        .select_related('sender', 'receiver')
        .order_by('-sent_at', '-id')
    )
    given_kudos = [
        record for record in kudos_records if record.sender_id == user.id
    ]
    received_kudos = [
        record for record in kudos_records if record.receiver_id == user.id
    ]
    kudos_given_points = sum(record.points for record in given_kudos)
    kudos_received_points = sum(record.points for record in received_kudos)

    kudos_feed = []
    for record in kudos_records[:INTERPERSONAL_KUDOS_FEED_LIMIT]:
        is_given = record.sender_id == user.id
        counterpart = record.receiver if is_given else record.sender
        counterpart_name = _interpersonal_display_name(counterpart)
        kudos_feed.append({
            'id': record.id,
            'is_given': is_given,
            'direction_label': 'Sent' if is_given else 'Received',
            'title': (
                f'You awarded {counterpart_name}'
                if is_given
                else f'{counterpart_name} awarded you'
            ),
            'points_display': f'+{record.points} Pts',
            'category': record.category or 'Uncategorised',
            'message_display': (
                record.message.strip()
                if record.message and record.message.strip()
                else 'No remarks provided.'
            ),
            'sent_at_display': timezone.localtime(
                record.sent_at
            ).strftime('%d %b %Y, %I:%M %p'),
            'border_color': '#16a34a' if is_given else '#6d3ec4',
            'badge_background': '#f0fdf4' if is_given else '#f3edff',
            'badge_color': '#16a34a' if is_given else '#6d3ec4',
        })

    # --- Mentorship: guided hours only count sessions logged as mentor ---
    sessions = list(
        MentorshipSession.objects.filter(mentor=user)
        .select_related('verified_by')
        .order_by('-session_date', '-id')
    )
    mentor_sessions = [
        session for session in sessions
        if session.participant_role == 'mentor'
    ]
    mentee_sessions = [
        session for session in sessions
        if session.participant_role == 'mentee'
    ]
    mentor_hours = sum(
        float(session.duration_hours or 0)
        for session in mentor_sessions
    )
    mentee_hours = sum(
        float(session.duration_hours or 0)
        for session in mentee_sessions
    )
    active_mentees = {
        _interpersonal_normalise_name(session.mentee_name)
        for session in mentor_sessions
        if session.session_date.year == current_year
        and _interpersonal_normalise_name(session.mentee_name)
    }
    verified_mentor_sessions = sum(
        1 for session in mentor_sessions if session.is_verified
    )
    mentorship_signoff_rate = (
        round((verified_mentor_sessions / len(mentor_sessions)) * 100)
        if mentor_sessions else None
    )

    mentorship_sessions = []
    for session in sessions[:INTERPERSONAL_MENTORSHIP_HISTORY_LIMIT]:
        if session.is_verified and session.verified_by_id:
            status_display = (
                f'Verified by {_interpersonal_display_name(session.verified_by)}'
            )
        elif session.is_verified:
            status_display = 'Verified'
        else:
            status_display = 'Pending Verification'

        mentorship_sessions.append({
            'id': session.id,
            'participant_name': session.mentee_name,
            'role_label': session.get_participant_role_display(),
            'counterpart_label': (
                'Mentee' if session.participant_role == 'mentor'
                else 'Mentor / Officer'
            ),
            'topic': session.topic,
            'notes_display': (
                session.notes or 'No session notes recorded.'
            ),
            'session_date_display': session.session_date.strftime('%d %b %Y'),
            'duration_display': _interpersonal_duration_display(
                session.duration_hours
            ),
            'status_display': status_display,
            'status_css': 'bg-success' if session.is_verified else 'bg-warning',
            'border_color': (
                '#16a34a' if session.participant_role == 'mentor'
                else '#6d3ec4'
            ),
        })

    return {
        'qr_eval_url': qr_eval_url,
        'mypsq_feedbacks': feedbacks,
        'mypsq_comments': [
            feedback for feedback in feedbacks if feedback.patient_comments
        ],
        'mypsq_count': mypsq_count,
        'mypsq_has_data': bool(mypsq_count),
        'mypsq_avg_score': mypsq_avg_score,
        'mypsq_pct': mypsq_pct,
        'mypsq_latest_date_display': mypsq_latest_date_display,
        'ei_reflections': ei_reflections,
        'ei_total_reflections': len(reflections),
        'ei_has_data': bool(reflections),
        'ei_domain_cards': ei_domain_cards,
        'ei_domain_count': ei_domain_count,
        'ei_composite': ei_composite,
        'ei_composite_display': (
            f'{ei_composite}%' if ei_composite is not None else 'No data'
        ),
        'ei_composite_status_display': ei_composite_status_display,
        'ei_composite_is_provisional': ei_composite_is_provisional,
        'ei_streak_weeks': ei_streak_weeks,
        'kudos_feed': kudos_feed,
        'kudos_feed_total': len(kudos_records),
        'kudos_has_activity': bool(kudos_records),
        'kudos_given_count': len(given_kudos),
        'kudos_received_count': len(received_kudos),
        'kudos_given_points': kudos_given_points,
        'kudos_received_points': kudos_received_points,
        'mentorship_year': current_year,
        'mentorship_sessions': mentorship_sessions,
        'mentorship_has_sessions': bool(sessions),
        'mentorship_mentor_count': len(mentor_sessions),
        'mentorship_mentee_count': len(mentee_sessions),
        'mentorship_mentor_hours': mentor_hours,
        'mentorship_mentor_hours_display': (
            _operational_metric_display(mentor_hours, decimal_places=1)
        ),
        'mentorship_mentee_hours': mentee_hours,
        'mentorship_mentee_hours_display': (
            _operational_metric_display(mentee_hours, decimal_places=1)
        ),
        'mentorship_active_mentees': len(active_mentees),
        'mentorship_mentor_verified_count': verified_mentor_sessions,
        'mentorship_signoff_rate': mentorship_signoff_rate,
        'mentorship_signoff_display': (
            f'{mentorship_signoff_rate}%'
            if mentorship_signoff_rate is not None else 'No data'
        ),
    }


@login_required(login_url='/login/')
def staff_dashboard_view(request):
    
    # Semakan peranan sebenar daripada profil pengguna (bukan sekadar session).
    profile = getattr(request.user, 'profile', None)
    if not profile or profile.role != 'STAFF':
        return redirect('login')

    section = request.GET.get('section', 'Dashboard')
    cusum_late_count = 0
    cusum_pending_count = 0
    
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

        elif form_type == 'doc':
            try:
                patient_ref = _required_text(
                    request.POST.get('patient_id'),
                    'ID pesakit',
                    max_length=100,
                )
                encounter_type = _optional_text(
                    request.POST.get('encounter_type'),
                    max_length=150,
                )
                encounter_at = _parse_datetime_value(
                    request.POST.get('encounter_at')
                )
                if not encounter_at:
                    raise ValidationError('Masa encounter diperlukan.')

                completed_at = _parse_datetime_value(
                    request.POST.get('completed_at')
                )
                if completed_at and completed_at < encounter_at:
                    raise ValidationError(
                        'Masa selesai tidak boleh lebih awal daripada masa encounter.'
                    )

                delay_hours = (
                    round(
                        (completed_at - encounter_at).total_seconds() / 3600.0,
                        2,
                    )
                    if completed_at else 0.0
                )
                reason = _optional_text(
                    request.POST.get('reason'),
                    max_length=100,
                )
                remarks = _optional_text(
                    request.POST.get('reason_remarks')
                )
                reason_for_delay = ' — '.join(
                    value for value in (reason, remarks) if value
                ) or None
            except ValidationError as exc:
                messages.error(
                    request,
                    f"❌ {exc.messages[0] if exc.messages else exc}",
                )
                return redirect(f'/staff/?section={section}')

            DocumentationLog.objects.create(
                officer=request.user,
                patient_id=_hash_patient_ref(patient_ref),
                encounter_type=encounter_type,
                encounter_at=encounter_at,
                completed_at=completed_at,
                delay_hours=delay_hours,
                reason_for_delay=reason_for_delay,
                delay_reason_category=reason,
                delay_reason_remarks=remarks,
                is_ontime=(delay_hours <= 4.0),
                is_completed=completed_at is not None,
            )
            messages.success(
                request,
                f"✅ Rekod dokumentasi {patient_ref} disimpan "
                f"({delay_hours:.1f} jam)."
            )

        elif form_type == 'cpd':
            # myCPD 2.0: domain wajib, mata dikira 100% di backend.
            kkm_domain = (request.POST.get('kkm_domain') or '').strip().upper()
            sub_category = (request.POST.get('sub_category') or '').strip().upper()
            role = (request.POST.get('role') or 'ATTENDEE').strip().upper()
            if role not in ('ATTENDEE', 'PRESENTER'):
                role = 'ATTENDEE'

            if kkm_domain not in KKM_CORE_DOMAINS:
                messages.error(request, "❌ Sila pilih domain KKM yang sah.")
                return redirect(f'/staff/?section={section}')

            mata = calculate_cpd_points(sub_category, role)
            if mata <= 0:
                messages.error(
                    request,
                    "❌ Duration tidak sah. Sila pilih duration yang disediakan.",
                )
                return redirect(f'/staff/?section={section}')

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
                category=category,
                sub_category=sub_category,
                kkm_domain=kkm_domain,
                role=role,
                event_date=event_date,
                end_date=end_date,
                certificate=certificate or None,
                status='PENDING',
            )
            messages.success(
                request,
                f"✅ {mata} mata CPD untuk '{tajuk}' disimpan "
                "(status: Pending Verification)."
            )

        elif form_type == 'sign_ot_note':
            doc_id = _parse_int(request.POST.get('doc_id'), 0)
            doc_item = DocumentationLog.objects.filter(id=doc_id, officer=request.user).first()
            if not doc_item:
                messages.error(request, "❌ Rekod kes tidak ditemui atau telah disahkan.")
                return redirect(f'/staff/?section={section}&tab=doc')

            now = timezone.now()
            start_time = doc_item.encounter_at or doc_item.created_at
            diff_hours = round((now - start_time).total_seconds() / 3600.0, 2)
            
            # KEPATUHAN SLA 4 JAM KKM
            is_ontime = (diff_hours <= 4.0)

            reason = (request.POST.get('delay_reason') or '').strip()
            remarks = (request.POST.get('delay_remarks') or '').strip()
            reason_text = ' — '.join(p for p in [reason, remarks] if p)

            doc_item.completed_at = now
            doc_item.delay_hours = diff_hours
            doc_item.is_ontime = is_ontime
            doc_item.is_completed = True
            if not is_ontime:
                doc_item.reason_for_delay = reason_text or "Disahkan melebihi had masa SLA 4 jam."
                doc_item.delay_reason_category = reason[:100] if reason else "Exceeded 4H"
                doc_item.delay_reason_remarks = remarks or None
            doc_item.save()

            # Cetuskan rantaian audit rawak 2 minit di tab Adherence To Policies
            case_label = (
                doc_item.procedure.procedure_name
                if getattr(doc_item, 'procedure', None)
                else (doc_item.encounter_type or 'Clinical Governance Protocol')
            )
            request.session['policy_audit_trigger'] = {
                'active': True,
                'doc_id': doc_item.id,
                'patient_id': doc_item.patient_id,
                'case_category': case_label,
                'triggered_at': now.isoformat(),
            }

            if is_ontime:
                messages.success(
                    request,
                    f"✅ Nota klinikal {doc_item.patient_id} disahkan On-Time ({diff_hours:.1f} jam). "
                    "Sila lengkapkan Micro-Audit Polisi (2 minit) di bawah."
                )
            else:
                messages.warning(
                    request,
                    f"⚠️ Nota klinikal {doc_item.patient_id} disahkan Lewat ({diff_hours:.1f} jam). "
                    "Sila lengkapkan Micro-Audit Polisi (2 minit) di bawah."
                )
            return redirect(f'/staff/?section={section}&tab=policy')

        elif form_type == 'policy':
            audit_mode = (request.POST.get('audit_mode') or '').strip()

            if audit_mode == 'timed_random':
                raw_presented = (request.POST.get('presented_questions') or '').strip()
                presented_qs = [
                    int(x) for x in raw_presented.split(',')
                    if x.strip().isdigit() and 1 <= int(x.strip()) <= 20
                ]
                if not presented_qs:
                    presented_qs = list(range(1, 6))

                ratios = {}
                selected_counts = {}
                max_counts = {}
                for q in presented_qs:
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

                prev_policy = PolicyDeclaration.objects.filter(officer=request.user).order_by('-submitted_at').first()
                baseline_domains = {
                    'a': prev_policy.score_domain_a if prev_policy else 85.0,
                    'b': prev_policy.score_domain_b if prev_policy else 85.0,
                    'c': prev_policy.score_domain_c if prev_policy else 85.0,
                    'd': prev_policy.score_domain_d if prev_policy else 85.0,
                    'e': prev_policy.score_domain_e if prev_policy else 85.0,
                }

                domain_scores = {}
                for key, questions in POLICY_DOMAIN_QUESTIONS.items():
                    sampled = [q for q in questions if q in ratios]
                    if sampled:
                        domain_scores[key] = round(
                            (sum(ratios[q] for q in sampled) / len(sampled)) * 100.0,
                            1,
                        )
                    else:
                        domain_scores[key] = baseline_domains[key]

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
                        for q in presented_qs
                    ])

                request.session.pop('policy_audit_trigger', None)
                avg_audit = round(sum(domain_scores.values()) / 5.0, 1)
                messages.success(
                    request,
                    f"✅ Micro-Audit Polisi selesai direkodkan ({len(presented_qs)} soalan rawak • Purata Pematuhan: {avg_audit}%)."
                )
                return redirect(f'/staff/?section={section}&tab=policy')

            # Mod standard (deklarasi penuh 20 soalan)
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
    donut_html = ""
    gauge_html = ""
    area_html = ""
    module_scores = None
    module_results = []
    scorable_modules_count = 0
    bento_cards = []
    home_alert_count = 0
    recent_activities = []
    dashboard_schedule_items = []
    acuity_caption = "Tiada rekod klinikal dalam pangkalan data."
    task_vel_doc_ontime = 0
    task_vel_doc_total = 0
    task_vel_doc_pct = 0
    task_vel_cpd_pts = 0
    task_vel_cpd_pct = 0
    task_vel_caption = "Tiada log produktiviti direkodkan."
    prod_trend_caption = "Tiada rekod produktiviti harian direkodkan."
    mypsq_gauge_caption = "Sasaran KKM: 4.5 / 5.0 | Tiada penilaian pesakit lagi."
    
    if section == 'Dashboard':
        # 0A. DONUT CHART SEBENAR: PATIENT ACUITY MIX (ProcedureLog)
        user_procs = list(ProcedureLog.objects.filter(officer=request.user))
        high_risk_cnt = 0
        moderate_cnt = 0
        controlled_cnt = 0

        for p in user_procs:
            is_high = (
                p.target_achieved is False
                or (p.hba1c_percent is not None and p.hba1c_percent >= 8.5)
                or (p.systolic_bp is not None and p.systolic_bp >= 160)
                or (p.ckd_stage in ('Stage 3B', 'Stage 4', 'Stage 5'))
                or (p.lvef_percent is not None and p.lvef_percent < 40)
                or (p.nyha_class in ('III', 'IV'))
            )
            has_clinical_params = any(
                val is not None for val in (
                    p.target_achieved, p.hba1c_percent, p.systolic_bp,
                    p.ckd_stage, p.lvef_percent, p.nyha_class
                )
            )
            if not has_clinical_params:
                if (p.score or 0) < 70.0:
                    high_risk_cnt += 1
                elif (p.score or 0) < 85.0:
                    moderate_cnt += 1
                else:
                    controlled_cnt += 1
            elif is_high:
                high_risk_cnt += 1
            else:
                is_mod = (
                    (p.hba1c_percent is not None and 7.0 <= p.hba1c_percent < 8.5)
                    or (p.systolic_bp is not None and 140 <= p.systolic_bp < 160)
                    or (p.ckd_stage in ('Stage 2', 'Stage 3A'))
                    or (p.nyha_class == 'II')
                )
                if is_mod:
                    moderate_cnt += 1
                else:
                    controlled_cnt += 1

        total_acuity = high_risk_cnt + moderate_cnt + controlled_cnt
        if total_acuity > 0:
            fig_donut = go.Figure(go.Pie(
                labels=["High Risk / Uncontrolled", "Moderate", "Controlled"],
                values=[high_risk_cnt, moderate_cnt, controlled_cnt],
                hole=0.65,
                marker=dict(colors=["#150a35", "#6d3ec4", "#fecdd3"]),
                textinfo='none'
            ))
            fig_donut.add_annotation(
                text=f"<b>{total_acuity}</b><br>Cases",
                x=0.5, y=0.5, font_size=14, showarrow=False
            )
            hr_pct = round((high_risk_cnt / total_acuity) * 100)
            ctrl_pct = round((controlled_cnt / total_acuity) * 100)
            acuity_caption = f"Berdasarkan {total_acuity} kes klinikal: {hr_pct}% Risiko Tinggi, {ctrl_pct}% Terkawal"
        else:
            fig_donut = go.Figure(go.Pie(
                labels=["Tiada Rekod"], values=[1], hole=0.65,
                marker=dict(colors=["#e7dff2"]), textinfo='none', hoverinfo='skip'
            ))
            fig_donut.add_annotation(text="<b>0</b><br>Cases", x=0.5, y=0.5, font_size=14, showarrow=False)

        fig_donut.update_layout(
            height=190, margin=dict(t=0, b=0, l=0, r=0), showlegend=True,
            legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5, font=dict(size=10)),
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)'
        )
        donut_html = fig_donut.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False})

        # 0B. AREA CHART SEBENAR: PRODUCTIVITY TREND (ProductivityLog)
        prod_series = list(
            ProductivityLog.objects.filter(officer=request.user)
            .order_by('-record_date', '-id')[:14]
        )
        prod_series.reverse()

        fig_area = go.Figure()
        if prod_series:
            tarikh_label = [item.record_date.strftime('%d %b') for item in prod_series]
            pesakit_data = [item.patients_cleared for item in prod_series]
            avg_cleared = round(sum(pesakit_data) / len(pesakit_data), 1)
            prod_trend_caption = f"Purata pelepasan pesakit: {avg_cleared} kes/hari ({len(prod_series)} log terkini)"

            fig_area.add_trace(go.Scatter(
                x=tarikh_label, y=pesakit_data, mode='lines+markers', fill='tozeroy',
                line=dict(color='#6d3ec4', width=3.5, shape='spline'),
                marker=dict(size=7, color='#150a35'),
                fillcolor='rgba(109, 62, 196, 0.16)',
                name='Patients Cleared'
            ))
        else:
            fig_area.add_trace(go.Scatter(
                x=["Tiada Data"], y=[0], mode='markers',
                marker=dict(size=8, color='#a577ff'), name='Tiada Rekod'
            ))

        fig_area.update_layout(
            height=220, margin=dict(t=10, b=0, l=0, r=10),
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            xaxis_title="", yaxis_title="Patients Cleared",
            xaxis=dict(showgrid=False), yaxis=dict(showgrid=True, gridcolor='#e7dff2', rangemode='tozero')
        )
        area_html = fig_area.to_html(full_html=False, include_plotlyjs=False, config={'displayModeBar': False})

        # 0C. GAUGE CHART SEBENAR: PURATA RATING PESAKIT MyPSQ (/ 5.0)
        mypsq_qs = list(MyPSQFeedback.objects.filter(doctor=request.user))
        if mypsq_qs:
            mypsq_val = round(sum(f.average_score for f in mypsq_qs) / len(mypsq_qs), 2)
            mypsq_gauge_caption = f"Sasaran KKM: 4.5 / 5.0 | Purata Semasa: {mypsq_val:.2f} ({len(mypsq_qs)} maklum balas)"
        else:
            mypsq_val = 0.0

        fig_gauge = go.Figure(go.Indicator(
            mode="gauge+number", value=mypsq_val,
            number={'valueformat': '.2f', 'font': {'size': 34, 'color': '#150a35', 'family': 'Inter'}},
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

        # 0D. DATA SEBENAR TASK VELOCITY (SLA DOKUMENTASI 4 JAM & SASARAN CPD 50 MATA)
        completed_docs_qs = DocumentationLog.objects.filter(officer=request.user, is_completed=True)
        task_vel_doc_total = completed_docs_qs.count()
        task_vel_doc_ontime = completed_docs_qs.filter(is_ontime=True).count()
        task_vel_doc_pct = int(round((task_vel_doc_ontime / task_vel_doc_total) * 100)) if task_vel_doc_total > 0 else 0

        cpd_trajectory = build_cpd_trajectory(request.user)
        task_vel_cpd_pts = cpd_trajectory['points']['approved']
        task_vel_cpd_pct = min(
            100, int(round(cpd_trajectory['points']['pct']))
        )

        if prod_series:
            avg_consult = round(sum(item.avg_consult_minutes for item in prod_series) / len(prod_series), 1)
            task_vel_caption = f"Purata masa konsultasi: {avg_consult} min/pesakit (Sasaran KKM: 10–15 min)"

        # 0E. DATA SEBENAR TODAY'S SCHEDULE & DUE TRACKER
        for p_doc in DocumentationLog.objects.filter(officer=request.user, is_completed=False).order_by('-encounter_at')[:2]:
            st_dt = timezone.localtime(p_doc.encounter_at or p_doc.created_at)
            dashboard_schedule_items.append({
                'time_label': st_dt.strftime('%I:%M %p'),
                'title': f"⏳ SLA 4-Hour Doc Due: {p_doc.patient_id}",
                'subtitle': p_doc.encounter_type or 'Outpatient Clinical Note',
                'badge': 'Sign Now',
                'url': '?section=Clinical&tab=doc',
                'is_urgent': True,
            })

        for fb in HODFeedback.objects.filter(receiver_staff=request.user, is_acknowledged=False).order_by('-created_at')[:2]:
            fb_dt = timezone.localtime(fb.created_at)
            dashboard_schedule_items.append({
                'time_label': fb_dt.strftime('%d %b'),
                'title': f"📩 HOD Notice: {fb.module_reference}",
                'subtitle': fb.message_text[:55] + ('...' if len(fb.message_text) > 55 else ''),
                'badge': 'Review',
                'url': '#hod-notice',
                'is_urgent': True,
            })

        for ms in MentorshipSession.objects.filter(mentor=request.user).order_by('-session_date')[:2]:
            if len(dashboard_schedule_items) >= 3:
                break
            dashboard_schedule_items.append({
                'time_label': ms.session_date.strftime('%d %b'),
                'title': f"🎓 Mentorship: {ms.mentee_name}",
                'subtitle': f"{ms.topic} ({ms.duration_hours:g}h)",
                'badge': 'Mentor',
                'url': '?section=Interpersonal&tab=mentorship',
                'is_urgent': False,
            })

        # 1. Tarik Skor 10 Modul & Snapshot CUSUM Dokumentasi Sebenar
        module_scores = get_module_scores(request.user)
        module_results = module_scores['modules']
        scorable_modules_count = module_scores['scorable_count']

        doc_snap = CusumSnapshot.objects.filter(
            officer=request.user,
            module='documentation',
        ).first()
        pending_doc_cnt = DocumentationLog.objects.filter(
            officer=request.user,
            is_completed=False,
        ).count()

        bento_meta = {
            'clinical_delivery': ('CLINICAL', '?section=Clinical&tab=delivery', 'Log Prosedur ➔'),
            'documentation': ('CLINICAL', '?section=Clinical&tab=doc', 'Semak Giliran ➔'),
            'cpd': ('CLINICAL', '?section=Clinical&tab=cpd', 'Tuntut CPD ➔'),
            'policies': ('CLINICAL', '?section=Clinical&tab=policy', 'Deklarasi SOP ➔'),
            'productivity': ('OPERATIONAL', '?section=Operational&tab=productivity', 'Log Produktiviti ➔'),
            'innovation': ('OPERATIONAL', '?section=Operational&tab=innovation', 'Log Inovasi ➔'),
            'mypsq': ('INTERPERSONAL', '?section=Interpersonal&tab=patient', 'Lihat MyPSQ ➔'),
            'emotional_intelligence': ('INTERPERSONAL', '?section=Interpersonal&tab=stress', 'Log Refleksi ➔'),
            'teamwork': ('INTERPERSONAL', '?section=Interpersonal&tab=teamwork', 'Beri Kudos ➔'),
            'mentorship': ('INTERPERSONAL', '?section=Interpersonal&tab=mentorship', 'Log Sesi ➔'),
        }

        for mod in module_results:
            key = mod['key']
            pillar, url, cta = bento_meta.get(key, ('MODULE', '?section=Clinical', 'Buka ➔'))
            score = mod['score']
            status = mod['status']

            # Tentukan warna & mesej coaching spesifik setiap modul
            if key == 'documentation' and pending_doc_cnt > 0:
                tier = 'WARN'
                badge_label = f"{pending_doc_cnt} Pending SLA"
                hint = f"{pending_doc_cnt} nota pesakit menunggu pengesahan SLA 4 jam."
            elif key == 'documentation' and doc_snap and doc_snap.state in ('WATCH', 'ALERT'):
                tier = 'ALERT' if doc_snap.state == 'ALERT' else 'WARN'
                badge_label = f"CUSUM {doc_snap.get_state_display()}"
                hint = f"Nilai CUSUM semasa {doc_snap.cusum_value:.4f}. Sila pantau ketepatan masa."
            elif status in ('NO_DATA', 'INVALID_DATA'):
                tier = 'WARN'
                badge_label = 'Perlu Rekod'
                hint = mod['reason'] or 'Belum ada rekod untuk tempoh semasa.'
            elif status == 'PENDING_VERIFICATION':
                tier = 'INFO'
                badge_label = 'Menunggu Sah'
                hint = mod['reason'] or 'Rekod dihantar, menunggu pengesahan HOD.'
            elif score is not None and score < 70.0:
                tier = 'ALERT'
                badge_label = 'Perlu Tindakan'
                hint = f"Skor semasa {score:.1f}% di bawah sasaran optimum (70%)."
            elif score is not None and score < 80.0:
                tier = 'WARN'
                badge_label = 'Boleh Ditingkatkan'
                hint = f"Skor semasa {score:.1f}%. Tambah bukti aktiviti untuk capai ≥80%."
            else:
                tier = 'OK'
                badge_label = 'Optimal'
                if key == 'documentation' and doc_snap:
                    hint = f"CUSUM In Control ({doc_snap.cusum_value:.4f}) • {doc_snap.observation_count} log."
                elif key == 'cpd':
                    hint = f"{int(mod['raw_value'] or 0)} / 50 mata CPD diluluskan."
                else:
                    hint = 'Prestasi berada pada tahap sasaran cemerlang.'

            if tier in ('ALERT', 'WARN'):
                home_alert_count += 1

            bento_cards.append({
                'key': key,
                'label': mod['label'],
                'pillar': pillar,
                'display': mod['display'],
                'tier': tier,
                'badge_label': badge_label,
                'hint': hint,
                'url': url,
                'cta': cta,
            })

        # 2. Suapan Aktiviti Terkini Sebenar (Recent Activity Feed)
        activity_pool = []
        for p in ProcedureLog.objects.filter(officer=request.user).order_by('-procedure_date', '-id')[:3]:
            activity_pool.append({
                'sort_dt': datetime.combine(p.procedure_date, datetime.min.time()),
                'date_display': p.procedure_date.strftime('%d %b %Y'),
                'module': 'Clinical Delivery',
                'title': f"Rekod klinikal: {p.procedure_name} ({p.score:.0f}%)",
                'url': '?section=Clinical&tab=delivery',
            })
        for d in DocumentationLog.objects.filter(officer=request.user).order_by('-created_at')[:3]:
            dt_val = timezone.localtime(d.created_at).replace(tzinfo=None)
            st_txt = 'Selesai On-Time' if (d.is_completed and d.is_ontime) else ('Selesai Lewat' if d.is_completed else 'Menunggu Sign-Off')
            activity_pool.append({
                'sort_dt': dt_val,
                'date_display': dt_val.strftime('%d %b %Y, %I:%M %p'),
                'module': 'Documentation',
                'title': f"Kes {d.patient_id} — {st_txt}",
                'url': '?section=Clinical&tab=doc',
            })
        for c in CPDClaim.objects.filter(officer=request.user).order_by('-submitted_at')[:3]:
            dt_val = timezone.localtime(c.submitted_at).replace(tzinfo=None)
            activity_pool.append({
                'sort_dt': dt_val,
                'date_display': dt_val.strftime('%d %b %Y'),
                'module': 'Knowledge (CPD)',
                'title': f"Tuntutan CPD: {c.topic} (+{c.points} pts • {c.get_status_display()})",
                'url': '?section=Clinical&tab=cpd',
            })
        for pr in ProductivityLog.objects.filter(officer=request.user).order_by('-record_date', '-id')[:2]:
            activity_pool.append({
                'sort_dt': datetime.combine(pr.record_date, datetime.min.time()),
                'date_display': pr.record_date.strftime('%d %b %Y'),
                'module': 'Productivity',
                'title': f"Log beban kerja: {pr.patients_cleared} pesakit ({pr.punctuality_status or 'Recorded'})",
                'url': '?section=Operational&tab=productivity',
            })

        activity_pool.sort(key=lambda item: item['sort_dt'], reverse=True)
        recent_activities = activity_pool[:6]

    # --- PERFORMANCE TAB (Holistic Benchmark 10 Modul - SPIDER WEB) ---
    elif section == 'Performance':
        # Satu sumber kebenaran: skor modul yang sama digunakan oleh kad,
        # radar, CUSUM dan jadual Module Readiness.
        module_scores = get_module_scores(request.user)
        module_results = module_scores['modules']
        by_key = module_scores['by_key']

        categories = [module['label'] for module in module_results]
        scores = [module['score'] for module in module_results]
        score_displays = [module['display'] for module in module_results]

        # Status tracker menggunakan rekod sebenar daripada enjin skor.
        has_proc = by_key['clinical_delivery']['has_records']
        has_cpd = by_key['cpd']['has_records']
        has_docs = by_key['documentation']['has_records']
        has_policy = by_key['policies']['has_records']
        has_prod = by_key['productivity']['has_records']
        has_innov = by_key['innovation']['has_records']
        has_mypsq = by_key['mypsq']['has_records']
        has_ei = by_key['emotional_intelligence']['has_records']
        has_team = by_key['teamwork']['has_records']
        has_mentor = by_key['mentorship']['has_records']

        # Active = mempunyai rekod; Ready = mempunyai skor sah (0-100).
        completed_modules_count = module_scores['recorded_count']
        scorable_modules_count = module_scores['scorable_count']
        portfolio_compliance_pct = int(
            round((scorable_modules_count / 10.0) * 100)
        )

        # Nilai ringkasan CUSUM dan Policy daripada modul sebenar.
        cusum_pct = by_key['clinical_delivery']['score'] or 0
        policy_pct = by_key['policies']['score'] or 0

        # Tarik Snapshot CUSUM Dokumentasi Sebenar bagi Pegawai Semasa
        snap = CusumSnapshot.objects.filter(
            officer=request.user,
            module='documentation',
        ).first()

        if snap:
            metadata = (
                snap.metadata
                if isinstance(snap.metadata, dict)
                else {}
            )
            cusum_state = snap.state
            cusum_state_label = snap.get_state_display()
            cusum_value = snap.cusum_value
            cusum_h = snap.h or 4.5
            cusum_watch_threshold = round(cusum_h / 2.0, 2)
            cusum_threshold_progress_pct = min(100.0, round((cusum_value / cusum_h) * 100.0, 1)) if cusum_h > 0 else 0.0
            cusum_alert_count = snap.alert_count
            cusum_observation_count = snap.observation_count
            cusum_skipped_count = snap.skipped_count
            cusum_late_count = max(
                0, _parse_int(metadata.get('late_count'))
            )
            cusum_pending_count = max(
                0, _parse_int(metadata.get('pending_count'))
            )
            cusum_last_event_at = snap.last_event_at
            cusum_last_alert_at = snap.last_alert_at
            cusum_computed_at = snap.computed_at
        else:
            cusum_state = 'INSUFFICIENT_DATA'
            cusum_state_label = 'Insufficient Data'
            cusum_value = 0.0
            cusum_h = 4.5
            cusum_watch_threshold = 2.25
            cusum_threshold_progress_pct = 0.0
            cusum_alert_count = 0
            cusum_observation_count = 0
            cusum_skipped_count = 0
            cusum_late_count = 0
            cusum_pending_count = 0
            cusum_last_event_at = None
            cusum_last_alert_at = None
            cusum_computed_at = None

        # Tutup poligon hanya jika semua 10 modul mempunyai skor. Jika ada
        # modul tanpa data, garis terputus (connectgaps=False) dan tiada fill
        # supaya jurang data tidak disalahertikan sebagai skor sifar.
        radar_complete = all(score is not None for score in scores)
        if radar_complete:
            categories = categories + [categories[0]]
            scores = scores + [scores[0]]
            score_displays = score_displays + [score_displays[0]]

        fig_radar = go.Figure()
        fig_radar.add_trace(go.Scatterpolar(
            r=scores,
            theta=categories,
            customdata=score_displays,
            fill='toself' if radar_complete else 'none',
            connectgaps=False,
            fillcolor='rgba(190, 18, 60, 0.25)', # Warna Merah Rose Lutsinar
            line=dict(color='#150a35', width=3), # Garisan Burgundy
            marker=dict(size=8, color='#6d3ec4', symbol='circle'),
            hovertemplate='%{theta}<br>%{customdata}<extra></extra>',
            name='My Score',
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

    # ==========================================
    # PROMOTION READINESS INDEX (PRI) CALCULATION (LIVE DB INTEGRATION)
    # ==========================================
    # Tarik data dari Enjin Pengira Skor (services.py) berdasarkan data sebenar!
    scores = calculate_pri_score(request.user)
    score_clinical = scores.get('score_clinical', 85)
    score_ops_coding = scores.get('score_operational', 80)
    score_interpersonal = scores.get('score_interpersonal', 88)
    score_cpd = scores.get('score_cpd')
    pri_score = scores.get('pri_score', 85)
    promotion_tier = scores.get('promotion_tier', 'Tier 1: Fast-Track Candidate')
    promotion_color = scores.get('promotion_color', '#16a34a')
    next_grade = scores.get('next_grade', 'UD48 (Fast-Track)')

    # ==========================================
    # LOGIK DIAGNOSTIK AI EVALUATOR (BERDASARKAN DATA SEBENAR & CUSUM)
    # ==========================================
    # 1. Semak status amaran awal CUSUM Documentation
    snap_check = CusumSnapshot.objects.filter(officer=request.user, module='documentation').first()
    cusum_st = snap_check.state if snap_check else 'INSUFFICIENT_DATA'

    if request.session.get('coaching_completed'):
        score_ops_coding = max(score_ops_coding, 88)
        ai_risk_tier = "BANNER"
        ai_alert_title = "Clinical Governance Standing: Remediation Verified"
        ai_alert_msg = f"Operational velocity ({score_ops_coding}%) has been restored above the 80% KKM benchmark. All core governance indicators are within optimal range."
        ai_action_text = ""
    else:
        # Penilaian gabungan: Skor Purata Keseluruhan + Real-Time CUSUM Drift
        if score_ops_coding < 65 or score_clinical < 65 or score_interpersonal < 65 or cusum_st == 'ALERT':
            ai_risk_tier = "RED"
            ai_alert_title = "Priority Remediation Notice: Performance Drift Detected"
            ai_alert_msg = "Core performance metrics have fallen below the 65% critical threshold or a CUSUM Alert was triggered. Immediate structured remediation is required."
            ai_action_text = "Launch Remediation Plan"
        elif score_ops_coding < 80 or score_clinical < 80 or score_interpersonal < 80 or cusum_st == 'WATCH':
            ai_risk_tier = "YELLOW"
            ai_alert_title = "Governance Advisory: Targeted Calibration Needed"
            ai_alert_msg = "Core clinical delivery is active, but certain metrics remain below the 80% benchmark or an early CUSUM drift was detected."
            ai_action_text = "Review Action Plan"
        else:
            # OPTIMAL: Tiada kad amaran dipaparkan
            ai_risk_tier = "NONE"
            ai_alert_title = ""
            ai_alert_msg = ""
            ai_action_text = ""

    # Tarik rekod DocumentationLog & ProductivityLog sebenar dari DB bagi staf ini
    doc_logs = DocumentationLog.objects.filter(officer=staff_user).order_by('-created_at') if staff_user else []
    prod_logs = (
        ProductivityLog.objects.filter(officer=staff_user)
        .order_by('-record_date', '-id')
        if staff_user else []
    )

    clinical_context = (
        _build_clinical_context(staff_user, doc_logs)
        if staff_user and section == 'Clinical' else {}
    )
    operational_context = (
        _build_operational_context(staff_user, prod_logs)
        if staff_user and section == 'Operational' else {}
    )
    interpersonal_context = (
        _build_interpersonal_context(staff_user, qr_eval_url)
        if staff_user and section == 'Interpersonal' else {}
    )
    patients_context = (
        _build_patients_context(staff_user, request.GET.get('patient_q', ''))
        if staff_user and section == 'Patients' else {}
    )

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

    policy_trigger = request.session.get('policy_audit_trigger') or {}
    policy_audit_active = bool(policy_trigger.get('active'))
    policy_audit_remaining_seconds = 120
    if policy_audit_active and policy_trigger.get('triggered_at'):
        try:
            trig_dt = datetime.fromisoformat(policy_trigger['triggered_at'])
            if timezone.is_naive(trig_dt):
                trig_dt = timezone.make_aware(trig_dt, timezone.get_default_timezone())
            elapsed_sec = (timezone.now() - trig_dt).total_seconds()
            policy_audit_remaining_seconds = max(0, int(120 - elapsed_sec))
        except (TypeError, ValueError):
            policy_audit_remaining_seconds = 120

    latest_policy_decl = (
        PolicyDeclaration.objects.filter(officer=request.user).order_by('-submitted_at').first()
        if section == 'Clinical' else None
    )

    context = {
        **clinical_context,
        **operational_context,
        **interpersonal_context,
        **patients_context,
        'policy_audit_active': policy_audit_active,
        'policy_audit_trigger': policy_trigger,
        'policy_audit_remaining_seconds': policy_audit_remaining_seconds,
        'latest_policy_decl': latest_policy_decl,
        'section': section,
        'calendar_events_json': calendar_events_json,
        'active_hod_feedbacks': active_hod_feedbacks,
        'doc_logs': doc_logs,
        'prod_logs': prod_logs,
        'chart_html': chart_html,
        'radar_html': radar_html,
        'donut_html': donut_html,
        'gauge_html': gauge_html,
        'area_html': area_html,
        'module_scores': module_scores,
        'module_results': module_results,
        'scorable_modules_count': scorable_modules_count,
        'bento_cards': bento_cards,
        'home_alert_count': home_alert_count,
        'recent_activities': recent_activities,
        'dashboard_schedule_items': dashboard_schedule_items,
        'acuity_caption': acuity_caption,
        'task_vel_doc_ontime': task_vel_doc_ontime,
        'task_vel_doc_total': task_vel_doc_total,
        'task_vel_doc_pct': task_vel_doc_pct,
        'task_vel_cpd_pts': task_vel_cpd_pts,
        'task_vel_cpd_pct': task_vel_cpd_pct,
        'task_vel_caption': task_vel_caption,
        'prod_trend_caption': prod_trend_caption,
        'mypsq_gauge_caption': mypsq_gauge_caption,
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

        # --- DATA SEBENAR CUSUM SHADOW MODE (STAFF) ---
        'cusum_snapshot': locals().get('snap', None),
        'cusum_state': locals().get('cusum_state', 'INSUFFICIENT_DATA'),
        'cusum_state_label': locals().get('cusum_state_label', 'Insufficient Data'),
        'cusum_value': locals().get('cusum_value', 0.0),
        'cusum_threshold_progress_pct': locals().get('cusum_threshold_progress_pct', 0.0),
        'cusum_alert_count': locals().get('cusum_alert_count', 0),
        'cusum_observation_count': locals().get('cusum_observation_count', 0),
        'cusum_skipped_count': locals().get('cusum_skipped_count', 0),
        'cusum_last_event_at': locals().get('cusum_last_event_at', None),
        'cusum_last_alert_at': locals().get('cusum_last_alert_at', None),
        'cusum_computed_at': locals().get('cusum_computed_at', None),
        'cusum_h': locals().get('cusum_h', 4.5),
        'cusum_watch_threshold': locals().get('cusum_watch_threshold', 2.25),
        'cusum_late_count': cusum_late_count,
        'cusum_pending_count': cusum_pending_count,
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
    hod_facility_label = _hod_scope_label(hod_scope, hod_state, hod_facility)

    # --- KUMPUL DATA MENTAH UNTUK STATISTIK & JADUAL PERINCIAN ---
    # Senarai fasiliti kekal sebagai registri kawasan; semua angka staf dan
    # markah di bawah diambil daripada akaun STAFF sebenar dalam skop HOD.
    active_facilities = len(klinik_dipaparkan) if pilihan_klinik == 'All Clinics' else 1
    dept = getattr(request.user.profile, 'department', 'FMS')
    senarai_doktor, staff_by_label = _build_hod_staff_picker(
        dept, pilihan_negeri, pilihan_klinik
    )
    staff_users = list(staff_by_label.values())
    senarai_doktor_lengkap = _build_hod_staff_records(
        staff_users,
        labels_by_user_id={
            staff.id: label for label, staff in staff_by_label.items()
        },
    )
    senarai_doktor_lengkap.sort(
        key=lambda row: (
            row['score'] is None,
            -(row['score'] or 0),
            row['name'].lower(),
        )
    )

    total_staff = len(senarai_doktor_lengkap)
    avg_performance = _mean_score(
        row['score'] for row in senarai_doktor_lengkap
    )
    evaluations_completed = sum(
        row['scorable_modules'] for row in senarai_doktor_lengkap
    )

    tier_1_count = sum(
        1 for row in senarai_doktor_lengkap
        if row['score'] is not None and row['score'] >= 85
    )
    tier_2_count = sum(
        1 for row in senarai_doktor_lengkap
        if row['score'] is not None and 70 <= row['score'] < 85
    )
    tier_3_count = sum(
        1 for row in senarai_doktor_lengkap
        if row['score'] is not None and row['score'] < 70
    )
    no_data_count = sum(
        1 for row in senarai_doktor_lengkap if row['score'] is None
    )

    def _roster_rate(count):
        return int(round((count / total_staff) * 100)) if total_staff else 0

    tier_distribution = {
        'tier_1_count': tier_1_count,
        'tier_2_count': tier_2_count,
        'tier_3_count': tier_3_count,
        'no_data_count': no_data_count,
        'tier_1_rate': _roster_rate(tier_1_count),
        'tier_2_rate': _roster_rate(tier_2_count),
        'tier_3_rate': _roster_rate(tier_3_count),
        'no_data_rate': _roster_rate(no_data_count),
    }

    recent_feedback = (
        HODFeedback.objects
        .filter(receiver_staff__in=staff_users)
        .select_related('sender_hod', 'receiver_staff')
        .order_by('-created_at')[:20]
    )
    severity_styles = {
        'KUDOS': ('status-excellent', 'Kudos'),
        'INFO': ('status-moderate', 'Info'),
        'WARNING': ('status-moderate', 'Warning'),
        'URGENT': ('status-needs-attention', 'Urgent'),
    }
    recent_evaluations = []
    for feedback in recent_feedback:
        reviewer = (
            f'{feedback.sender_hod.first_name} '
            f'{feedback.sender_hod.last_name}'
        ).strip() or feedback.sender_hod.username
        receiver = (
            f'{feedback.receiver_staff.first_name} '
            f'{feedback.receiver_staff.last_name}'
        ).strip() or feedback.receiver_staff.username
        css_class, label = severity_styles.get(
            feedback.severity, ('status-moderate', feedback.severity)
        )
        if feedback.is_acknowledged:
            label = f'{label} • Acknowledged'
        recent_evaluations.append({
            'created_at': timezone.localtime(feedback.created_at),
            'receiver_name': f'Dr. {receiver}',
            'module': feedback.module_reference,
            'reviewer': f'Dr. {reviewer}',
            'status': label,
            'status_class': css_class,
        })

    cusum_alert_rows = _hod_cusum_alerts(staff_users)
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
        'pending_validation_count': _hod_pending_validation_count(staff_users),
        'fast_track_count': tier_1_count,
        'action_urgent_count': len(cusum_alert_rows),
        'cusum_top_alerts': [
            {
                'officer_name': 'Dr. {}'.format(
                    (
                        f'{alert.officer.first_name} '
                        f'{alert.officer.last_name}'
                    ).strip() or alert.officer.username
                ),
                'alert_count': alert.alert_count,
            }
            for alert in cusum_alert_rows
        ],

        # Data Mentah Untuk Dibedah di Fasa 2 (HTML)
        'klinik_dipaparkan': klinik_dipaparkan,
        'senarai_doktor_lengkap': senarai_doktor_lengkap,
        'tier_distribution': tier_distribution,
        'recent_evaluations': recent_evaluations,
        'dept': dept,
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
        score_cpd = scores.get('score_cpd') or 0
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

    # Tapis staf sebenar mengikut skop negeri/fasiliti HOD.
    dept = getattr(request.user.profile, 'department', 'FMS')
    senarai_doktor, staff_by_label = _build_hod_staff_picker(
        dept, pilihan_negeri, pilihan_klinik
    )
    staff_in_scope = list(staff_by_label.values())

    # Semak sama ada wujud sebarang alert CUSUM sebenar
    cusum_breaches = _hod_cusum_alerts(staff_in_scope)
    cusum_urgent_count = len(cusum_breaches)
    cusum_top_alerts = [
        {
            'officer_name': 'Dr. {}'.format(
                (
                    f"{breach.officer.first_name} "
                    f"{breach.officer.last_name}"
                ).strip() or breach.officer.username
            ),
            'alert_count': breach.alert_count,
        }
        for breach in cusum_breaches
    ]

    scorecards, staff_summaries = _build_hod_module_scorecards(
        list(staff_by_label.values()),
        labels_by_user_id={
            staff.id: label for label, staff in staff_by_label.items()
        },
    )
    score_by_key = {card['key']: card['score'] for card in scorecards}
    fast_track_count = sum(
        1 for summary in staff_summaries
        if summary['module_average'] is not None
        and summary['module_average'] >= 85
    )

    staff_ids = [staff.id for staff in staff_in_scope]
    pending_procedure_count = 0
    pending_cpd_count = 0
    pending_policy_count = 0
    if staff_ids:
        pending_procedure_count = ProcedureLog.objects.filter(
            officer_id__in=staff_ids, is_verified=False
        ).count()
        pending_cpd_count = CPDClaim.objects.filter(
            officer_id__in=staff_ids, status='PENDING'
        ).count()
        pending_policy_count = PolicyDeclaration.objects.filter(
            officer_id__in=staff_ids, is_verified=False
        ).count()
    pending_validation_count = (
        pending_procedure_count + pending_cpd_count + pending_policy_count
    )

    red_flags = []

    # Jika ada breach CUSUM sebenar, papar pegawai tersebut
    for b in cusum_breaches:
        o_name = f"Dr. {b.officer.first_name} {b.officer.last_name}".strip() or b.officer.username
        alert_time = b.last_alert_at.strftime('%d %b %Y, %I:%M %p') if b.last_alert_at else 'Recent Alert'
        red_flags.append({
            'type': 'CUSUM Alert (Documentation SLA)',
            'officer': o_name,
            'facility': getattr(b.officer.profile, 'assigned_facility', '') or pilihan_klinik,
            'severity': 'Urgent',
            'time': alert_time,
            'detail': f'4-Hour Documentation SLA breached (CUSUM value: {b.cusum_value:.4f}, Alert #{b.alert_count}).'
        })

    if fast_track_count:
        red_flags.append({
            'type': 'Fast-Track Candidates (Module Average ≥ 85%)',
            'officer': f'{fast_track_count} Staff',
            'facility': pilihan_klinik,
            'severity': 'Notice',
            'time': 'Live',
            'detail': f'{fast_track_count} officer(s) in scope currently meet the ≥85% fast-track review threshold.',
        })

    if pending_validation_count:
        detail_parts = []
        if pending_procedure_count:
            detail_parts.append(f'{pending_procedure_count} procedure log(s)')
        if pending_cpd_count:
            detail_parts.append(f'{pending_cpd_count} CPD claim(s)')
        if pending_policy_count:
            detail_parts.append(f'{pending_policy_count} policy declaration(s)')
        red_flags.append({
            'type': 'Pending Approval Log',
            'officer': 'Multiple Officers',
            'facility': pilihan_klinik,
            'severity': 'Warning',
            'time': 'Live',
            'detail': f'{pending_validation_count} item(s) require HOD validation: {", ".join(detail_parts)}.',
        })

    doc_score = score_by_key.get('documentation')
    if doc_score is not None and doc_score < 80:
        red_flags.append({
            'type': 'SOP Documentation Drop',
            'officer': 'Department Aggregate',
            'facility': pilihan_klinik,
            'severity': 'Warning',
            'time': 'Live',
            'detail': f'Documentation quality is at {doc_score:.1f}%, below the 80% target.',
        })

    if not red_flags:
        red_flags.append({
            'type': 'CUSUM Status (Documentation SLA)',
            'officer': 'All Active Officers',
            'facility': pilihan_klinik if pilihan_klinik != 'All Clinics' else 'All Monitored Facilities',
            'severity': 'Notice',
            'time': 'Active Surveillance',
            'detail': 'Zero CUSUM breaches, pending validations, or documentation drop detected for this scope.'
        })

    context = {
        'senarai_negeri': senarai_negeri,
        'pilihan_negeri': pilihan_negeri,
        'senarai_klinik': ["All Clinics"] + senarai_klinik,
        'pilihan_klinik': pilihan_klinik,
        'hod_name': hod_name,
        'hod_facility_label': _hod_scope_label(
            hod_scope, hod_state, hod_facility
        ),
        'red_flags': red_flags,
        'dept': dept,
        'action_urgent_count': cusum_urgent_count,
        'fast_track_count': fast_track_count,
        'pending_validation_count': pending_validation_count,
        'cusum_top_alerts': cusum_top_alerts,
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
    pilihan_klinik = hod_facility if (hod_scope == 'FACILITY' and hod_facility) else request.GET.get('clinic', 'All Clinics')

    hod_name = f"{request.user.first_name} {request.user.last_name}".strip() or request.user.username
    hod_facility_label = _hod_scope_label(hod_scope, hod_state, hod_facility)
    dept = getattr(request.user.profile, 'department', 'FMS')

    senarai_doktor, staff_by_label = _build_hod_staff_picker(
        dept, pilihan_negeri, pilihan_klinik
    )
    staff_users = list(staff_by_label.values())
    senarai_doktor_lengkap = _build_hod_staff_records(
        staff_users,
        labels_by_user_id={
            staff.id: label for label, staff in staff_by_label.items()
        },
    )
    senarai_doktor_lengkap.sort(key=lambda row: row['name'].lower())
    cusum_alert_rows = _hod_cusum_alerts(staff_users)

    context = {
        'senarai_negeri': senarai_negeri,
        'pilihan_negeri': pilihan_negeri,
        'pilihan_klinik': pilihan_klinik,
        'hod_name': hod_name,
        'hod_facility_label': hod_facility_label,
        'senarai_doktor': senarai_doktor,
        'senarai_doktor_lengkap': senarai_doktor_lengkap,
        'dept': dept,
        'pending_validation_count': _hod_pending_validation_count(staff_users),
        'fast_track_count': sum(
            1 for row in senarai_doktor_lengkap
            if row['score'] is not None and row['score'] >= 85
        ),
        'action_urgent_count': len(cusum_alert_rows),
        'cusum_top_alerts': [
            {
                'officer_name': 'Dr. {}'.format(
                    (
                        f'{alert.officer.first_name} '
                        f'{alert.officer.last_name}'
                    ).strip() or alert.officer.username
                ),
                'alert_count': alert.alert_count,
            }
            for alert in cusum_alert_rows
        ],
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

