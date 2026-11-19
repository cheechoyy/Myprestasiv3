from datetime import date, datetime, timedelta
import math

from django.db.models import Q, Sum
from django.utils import timezone

from .models import (
    ProcedureLog, PolicyDeclaration, DocumentationLog,
    CPDClaim, ProductivityLog, InnovationProject,
    MyPSQFeedback, EmotionalReflection, PeerKudos, MentorshipSession,
    KKM_CORE_DOMAINS, KKM_CORE_DOMAIN_LABELS, KKM_CORE_DOMAIN_WEIGHTS,
    KKM_RARE_DOMAINS,
    calculate_cpd_claim_points as _calculate_cpd_claim_points,
)


CPD_TARGET_POINTS = 50
CPD_POINTS_WEIGHT = 0.60
CPD_COVERAGE_WEIGHT = 0.40


MODULE_ORDER = (
    ('clinical_delivery', 'Clinical Delivery'),
    ('documentation', 'Documentation'),
    ('cpd', 'Knowledge & Competency (CPD)'),
    ('policies', 'Adherence To Policies'),
    ('productivity', 'Productivity'),
    ('innovation', 'Innovation'),
    ('mypsq', 'Patient Satisfaction (MyPSQ)'),
    ('emotional_intelligence', 'Stress Management (EI)'),
    ('teamwork', 'Teamwork & Collaboration'),
    ('mentorship', 'Training & Mentorship'),
)

SCORE_STATUSES = (
    'OK',
    'ZERO',
    'NO_DATA',
    'INVALID_DATA',
    'PROVISIONAL',
    'PENDING_VERIFICATION',
)


def _reflection_composite(reflection):
    """Purata domain EI yang tersedia; kekalkan keserasian rekod lama."""
    values = [
        reflection.self_awareness_score,
        reflection.self_regulation_score,
        reflection.empathy_score,
        reflection.deescalation_score,
    ]
    values = [value for value in values if value is not None]
    return sum(values) / len(values) if values else 0.0


def calculate_pri_score(user):
    """
    Enjin Pengira Skor Bersepadu MyPrestasi berasaskan 3 Tunjang Utama:
    1. Clinical Excellence (4 Sub-modul)
    2. Operational (2 Sub-modul)
    3. Interpersonal Excellence (4 Sub-modul)
    """

    # ==========================================
    # TUNJANG 1: CLINICAL EXCELLENCE (4 Sub-modul)
    # ==========================================
    # Sub 1: Clinical Delivery (CUSUM)
    procedures = ProcedureLog.objects.filter(officer=user)
    score_delivery = (sum([p.score for p in procedures]) / procedures.count()) if procedures.exists() else 88.0

    # Sub 2: Knowledge & Competency (CPD Claims)
    # Skor komposit 60% mata + 40% liputan domain teras. Tiada data sah =
    # None, dan komponen ini dikecualikan daripada purata PRI.
    cpd_trajectory = build_cpd_trajectory(user)
    score_knowledge = cpd_trajectory['score']['final']

    # Sub 3: Adherence To Policies & Protocols
    policy = PolicyDeclaration.objects.filter(officer=user).order_by('-submitted_at').first()
    if policy:
        score_policy = (policy.score_domain_a + policy.score_domain_b + 
                        policy.score_domain_c + policy.score_domain_d + 
                        policy.score_domain_e) / 5.0
    else:
        score_policy = 85.0

    # Sub 4: Documentation
    docs = DocumentationLog.objects.filter(officer=user)
    score_doc = (sum([100.0 if d.is_ontime else 60.0 for d in docs]) / docs.count()) if docs.exists() else 82.0

    # PURATA TUNJANG 1: CLINICAL EXCELLENCE
    # CPD yang tiada data sah tidak diberi 0 atau fallback 90; ia dikecualikan.
    clinical_components = [
        score_delivery, score_knowledge, score_policy, score_doc,
    ]
    available_clinical = [
        value for value in clinical_components if value is not None
    ]
    score_clinical = (
        int(round(sum(available_clinical) / len(available_clinical)))
        if available_clinical else 0
    )


    # ==========================================
    # TUNJANG 2: OPERATIONAL (2 Sub-modul)
    # ==========================================
    # Sub 1: Productivity
    prod = ProductivityLog.objects.filter(officer=user).order_by('-record_date').first()
    score_productivity = prod.punctuality_rate if prod else 80.0

    # Sub 2: Innovation
    innov = InnovationProject.objects.filter(officer=user)
    score_innovation = (sum([i.score for i in innov]) / innov.count()) if innov.exists() else 75.0

    # PURATA TUNJANG 2: OPERATIONAL
    score_operational = int((score_productivity + score_innovation) / 2.0)


    # ==========================================
    # TUNJANG 3: INTERPERSONAL EXCELLENCE (4 Sub-modul)
    # ==========================================
    # Sub 1: Patient Satisfaction (MyPSQ)
    mypsq = MyPSQFeedback.objects.filter(doctor=user)
    score_patient = ((sum([f.average_score for f in mypsq]) / mypsq.count()) / 5.0 * 100.0) if mypsq.exists() else 92.0

    # Sub 2: Stress Management
    emotions = list(EmotionalReflection.objects.filter(officer=user))
    score_stress = (
        sum(_reflection_composite(reflection) for reflection in emotions)
        / len(emotions)
        if emotions else 85.0
    )

    # Sub 3: Teamwork & Collaboration
    kudos = PeerKudos.objects.filter(receiver=user)
    score_teamwork = min(100.0, (sum([k.points for k in kudos]) / 30.0) * 100.0) if kudos.exists() else 88.0

    # Sub 4: Training & Mentorship
    mentorships = MentorshipSession.objects.filter(mentor=user)
    score_mentorship = min(100.0, (sum([m.duration_hours for m in mentorships]) / 10.0) * 100.0) if mentorships.exists() else 80.0

    # PURATA TUNJANG 3: INTERPERSONAL EXCELLENCE
    score_interpersonal = int((score_patient + score_stress + score_teamwork + score_mentorship) / 4.0)


    # ==========================================
    # FORMULA PROMOTION READINESS INDEX (PRI)
    # Wajaran: 40% Clinical + 30% Operational + 30% Interpersonal
    # ==========================================
    pri_score = int((0.40 * score_clinical) + 
                    (0.30 * score_operational) + 
                    (0.30 * score_interpersonal))

    # Pengelompokan Tier Promosi
    if pri_score >= 85:
        tier = "Tier 1: Fast-Track Candidate"
        color = "#16a34a"
        next_grade = "UD48 (Fast-Track)"
    elif pri_score >= 70:
        tier = "Tier 2: On-Track (Time-Based)"
        color = "#d97706"
        next_grade = "UD48 (Time-Based)"
    else:
        tier = "Tier 3: Action Required"
        color = "#6d3ec4"
        next_grade = "UD48 (Deferred)"

    return {
        'score_clinical': score_clinical,
        'score_operational': score_operational,
        'score_interpersonal': score_interpersonal,
        'score_cpd': (
            int(round(score_knowledge))
            if score_knowledge is not None else None
        ),
        'cpd_trajectory': cpd_trajectory,
        'pri_score': pri_score,
        'promotion_tier': tier,
        'promotion_color': color,
        'next_grade': next_grade,
    }


def _clamp_score(value):
    """Kekalkan skor dalam julat 0-100."""
    return max(0.0, min(100.0, float(value)))


def _round_score(value, places=1):
    if value is None:
        return None
    return round(float(value), places)


def _local_date(value):
    if value is None:
        return None
    if timezone.is_aware(value):
        return timezone.localtime(value).date()
    return value.date()


def _module_result(
    key,
    label,
    score=None,
    n=0,
    denominator=0,
    raw_value=None,
    status='NO_DATA',
    reason=None,
    is_provisional=False,
    has_records=False,
    source='',
    target=None,
    breakdown=None,
):
    rounded_score = _round_score(score)
    rounded_raw = (
        round(float(raw_value), 2)
        if isinstance(raw_value, (int, float))
        else raw_value
    )
    return {
        'key': key,
        'label': label,
        'score': rounded_score,
        'display': 'No Data' if rounded_score is None else f'{rounded_score:.1f}%',
        'n': int(n or 0),
        'denominator': int(denominator or 0),
        'raw_value': rounded_raw,
        'status': status if status in SCORE_STATUSES else 'NO_DATA',
        'reason': reason,
        'is_provisional': bool(is_provisional),
        'has_records': bool(has_records),
        'source': source,
        'target': target,
        'breakdown': breakdown,
    }


def _score_status(score, provisional=False):
    if score is None:
        return 'NO_DATA'
    if provisional:
        return 'PROVISIONAL'
    return 'ZERO' if score == 0 else 'OK'


def calculate_cpd_points(sub_category, role):
    """Public API: mata CPD daripada Duration + Role (0 jika tidak dikenali)."""
    points = _calculate_cpd_claim_points(sub_category, role)
    return points if points is not None else 0


def _resolve_cpd_as_of(as_of):
    if as_of is None:
        return timezone.localdate()
    if isinstance(as_of, datetime):
        if timezone.is_naive(as_of):
            as_of = timezone.make_aware(as_of)
        return timezone.localtime(as_of).date()
    if isinstance(as_of, date):
        return as_of
    raise TypeError('as_of mesti date, datetime atau None')


def eligible_cpd_claims(user, as_of=None):
    """Satu gate tunggal: hanya claim tahun semasa dengan domain teras sah."""
    as_of = _resolve_cpd_as_of(as_of)
    return CPDClaim.objects.filter(
        officer=user,
        event_date__year=as_of.year,
        kkm_domain__in=KKM_CORE_DOMAINS,
    )


def _cpd_legacy_filter():
    return Q(kkm_domain__isnull=True) | Q(kkm_domain='')


def build_cpd_trajectory(user, as_of=None):
    """
    Trajektori CPD tahunan:
      - 60% mata (approved / 50)
      - 40% liputan domain teras berwajaran (rare = 1.5x)
    Rekod tanpa ``kkm_domain`` dianggap legacy dan tidak dikira.
    """
    as_of = _resolve_cpd_as_of(as_of)
    all_claims = CPDClaim.objects.filter(
        officer=user, event_date__year=as_of.year
    )
    has_any_claims = all_claims.exists()
    legacy_claims = all_claims.filter(_cpd_legacy_filter())
    legacy_ignored_count = legacy_claims.count()
    legacy_ignored_total = CPDClaim.objects.filter(
        officer=user
    ).filter(_cpd_legacy_filter()).count()

    eligible_qs = eligible_cpd_claims(user, as_of=as_of)
    approved_claims = list(eligible_qs.filter(status='APPROVED'))
    pending_claims = list(eligible_qs.filter(status='PENDING'))
    approved_points = sum(claim.points for claim in approved_claims)
    pending_points = sum(claim.points for claim in pending_claims)
    approved_count = len(approved_claims)
    pending_count = len(pending_claims)

    approved_by_domain = {
        code: {'points': 0, 'count': 0} for code in KKM_CORE_DOMAINS
    }
    pending_by_domain = {
        code: {'points': 0, 'count': 0} for code in KKM_CORE_DOMAINS
    }
    for claim in approved_claims:
        bucket = approved_by_domain[claim.kkm_domain]
        bucket['points'] += claim.points
        bucket['count'] += 1
    for claim in pending_claims:
        bucket = pending_by_domain[claim.kkm_domain]
        bucket['points'] += claim.points
        bucket['count'] += 1

    per_domain = []
    covered_domains = []
    pending_domains = []
    missing_domains = []
    for code in KKM_CORE_DOMAINS:
        approved_bucket = approved_by_domain[code]
        pending_bucket = pending_by_domain[code]
        if approved_bucket['count']:
            status = 'COVERED'
            covered_domains.append(code)
        elif pending_bucket['count']:
            status = 'PENDING'
            pending_domains.append(code)
        else:
            status = 'MISSING'
            missing_domains.append(code)
        per_domain.append({
            'code': code,
            'label': KKM_CORE_DOMAIN_LABELS.get(code, code),
            'weight': KKM_CORE_DOMAIN_WEIGHTS.get(code, 1.0),
            'is_rare': code in KKM_RARE_DOMAINS,
            'status': status,
            'approved_points': approved_bucket['points'],
            'approved_count': approved_bucket['count'],
            'pending_points': pending_bucket['points'],
            'pending_count': pending_bucket['count'],
        })

    weight_total = sum(
        KKM_CORE_DOMAIN_WEIGHTS.get(code, 1.0)
        for code in KKM_CORE_DOMAINS
    )
    weight_covered = sum(
        KKM_CORE_DOMAIN_WEIGHTS.get(code, 1.0)
        for code in covered_domains
    )
    coverage_pct = (
        _clamp_score((weight_covered / weight_total) * 100.0)
        if weight_total else 0.0
    )
    unweighted_coverage_pct = (
        _clamp_score(
            (len(covered_domains) / len(KKM_CORE_DOMAINS)) * 100.0
        ) if KKM_CORE_DOMAINS else 0.0
    )
    points_pct = (
        _clamp_score(
            (approved_points / float(CPD_TARGET_POINTS)) * 100.0
        ) if approved_count else 0.0
    )

    final_score = None
    if approved_count:
        final_score = _round_score(
            (CPD_POINTS_WEIGHT * points_pct)
            + (CPD_COVERAGE_WEIGHT * coverage_pct)
        )

    if final_score is None:
        if pending_count:
            status = 'PENDING_VERIFICATION'
            reason = (
                'Tiada mata CPD diluluskan; terdapat claim menunggu '
                'verifikasi.'
            )
        elif legacy_ignored_count:
            status = 'NO_DATA'
            reason = (
                f'{legacy_ignored_count} rekod legacy tanpa domain KKM '
                'tidak dikira.'
            )
        else:
            status = 'NO_DATA'
            reason = 'Tiada claim CPD dengan domain KKM bagi tahun semasa.'
    else:
        if final_score >= 100:
            status = 'COMPLETED'
            reason = 'Sasaran mata dan liputan semua domain teras dicapai.'
        elif points_pct >= 100 and coverage_pct < 100:
            status = 'POINTS_MET_COVERAGE_GAP'
            reason = 'Sasaran mata dicapai tetapi liputan domain belum lengkap.'
        elif coverage_pct >= 100 and points_pct < 100:
            status = 'COVERAGE_MET_POINTS_GAP'
            reason = 'Semua domain diliputi tetapi sasaran mata belum dicapai.'
        elif final_score == 0:
            status = 'ZERO'
            reason = 'Rekod sah wujud tetapi mata dan liputan domain sifar.'
        else:
            status = 'BEHIND'
            reason = 'Mata atau liputan domain masih di bawah sasaran.'

    rare_missing = [
        code for code in missing_domains if code in KKM_RARE_DOMAINS
    ]
    return {
        'as_of': as_of,
        'target_points': CPD_TARGET_POINTS,
        'points': {
            'approved': approved_points,
            'pending': pending_points,
            'approved_count': approved_count,
            'pending_count': pending_count,
            'target': CPD_TARGET_POINTS,
            'pct': _round_score(points_pct),
        },
        'coverage': {
            'core_total': len(KKM_CORE_DOMAINS),
            'covered_count': len(covered_domains),
            'covered': covered_domains,
            'pending_covered': pending_domains,
            'missing': missing_domains,
            'rare_missing': rare_missing,
            'weight_total': round(weight_total, 2),
            'weight_covered': round(weight_covered, 2),
            'weighted_pct': _round_score(coverage_pct),
            'unweighted_pct': _round_score(unweighted_coverage_pct),
        },
        'per_domain': per_domain,
        'score': {
            'final': final_score,
            'points_component': _round_score(
                CPD_POINTS_WEIGHT * points_pct
            ),
            'coverage_component': _round_score(
                CPD_COVERAGE_WEIGHT * coverage_pct
            ),
            'points_pct': _round_score(points_pct),
            'coverage_pct': _round_score(coverage_pct),
        },
        'status': status,
        'reason': reason,
        'has_any_claims': has_any_claims,
        'has_eligible_claims': bool(approved_count or pending_count),
        'legacy_ignored_count': legacy_ignored_count,
        'legacy_ignored_total': legacy_ignored_total,
    }


def _kkm_hba1c_result(user, as_of=None):
    """Kira attainment KKM Indicator-1 daripada bacaan HbA1c pesakit.

    Kohort menggunakan pesakit T2DM yang mempunyai bacaan asas HbA1c > 10.0%
    apabila kohort tersebut wujud; jika belum ada, pesakit tidak terkawal
    (HbA1c > 7.0%) digunakan sebagai kohort sandaran. Sasaran adalah
    pengurangan >= 1.0% atau HbA1c susulan <= 7.0% dalam tempoh 183 hari.

    Fungsi ini tidak menggunakan data preview/simulasi. Ia mengembalikan
    numerator, denominator dan senarai kohort supaya paparan Clinical dan
    agregasi HOD menggunakan sumber yang sama.
    """
    if as_of is None:
        as_of = timezone.localdate()
    elif isinstance(as_of, datetime):
        if timezone.is_naive(as_of):
            as_of = timezone.make_aware(as_of)
        as_of = timezone.localtime(as_of).date()
    elif not isinstance(as_of, date):
        raise TypeError('as_of mesti date, datetime atau None')

    procedures = list(
        ProcedureLog.objects.filter(
            officer=user,
            procedure_date__lte=as_of,
        )
        .prefetch_related('doc_logs')
        .order_by('procedure_date', 'id')
    )

    result = {
        'has_records': bool(procedures),
        'evaluated_cohort': [],
        'pending_cohort': [],
        'excluded_count': 0,
        'cohort_mode_label': 'Indicator-1 Cohort (Baseline HbA1c > 10.0%)',
        'numerator': 0,
        'denominator': 0,
        'score': None,
        'status': 'NO_DATA',
        'reason': 'Tiada rekod prosedur.',
    }
    if not procedures:
        return result

    patient_groups = {}
    for procedure in procedures:
        linked_docs = list(procedure.doc_logs.all())
        doc_label = linked_docs[0].patient_id if linked_docs else ''
        patient_key = (
            procedure.patient_ref
            or doc_label
            or f'CASE-{procedure.id:04d}'
        )
        if patient_key not in patient_groups:
            patient_groups[patient_key] = {
                'patient_ref': (
                    procedure.patient_ref
                    or (doc_label.split(' (')[0].strip() if doc_label else patient_key)
                ),
                'visits': [],
            }
        patient_groups[patient_key]['visits'].append(procedure)

    eligible_patients = []
    for patient_key, patient_data in patient_groups.items():
        visits = patient_data['visits']
        is_excluded = False
        for visit in visits:
            diagnosis_blob = (
                f"{visit.primary_diagnosis or ''} {visit.procedure_name or ''}"
            ).lower()
            comorbidities = [
                str(item).lower() for item in (visit.comorbidities or [])
            ]
            if (
                't1dm' in diagnosis_blob
                or 'type 1' in diagnosis_blob
                or 'gdm' in diagnosis_blob
                or 'gestational' in diagnosis_blob
                or any(
                    'severe mental' in item or 'palliative' in item
                    for item in comorbidities
                )
            ):
                is_excluded = True
                break
        if is_excluded:
            result['excluded_count'] += 1
            continue

        hba1c_visits = [v for v in visits if v.hba1c_percent is not None]
        if not hba1c_visits:
            continue
        eligible_patients.append({
            'patient_ref': patient_data['patient_ref'],
            'hba1c_visits': hba1c_visits,
        })

    strict_cohort = [
        item for item in eligible_patients
        if float(item['hba1c_visits'][0].hba1c_percent) > 10.0
    ]
    if strict_cohort:
        active_cohort = strict_cohort
    else:
        active_cohort = [
            item for item in eligible_patients
            if float(item['hba1c_visits'][0].hba1c_percent) > 7.0
        ]
        if active_cohort:
            result['cohort_mode_label'] = (
                'T2DM Uncontrolled Cohort (Baseline HbA1c > 7.0%)'
            )

    if not active_cohort:
        if result['excluded_count'] and not eligible_patients:
            result['reason'] = 'Semua pesakit dikecualikan daripada kohort KKM.'
        elif eligible_patients:
            result['reason'] = (
                'Tiada kohort T2DM tidak terkawal (HbA1c > 7.0%) '
                'bagi tempoh semasa.'
            )
        else:
            result['reason'] = 'Tiada bacaan HbA1c dalam rekod prosedur.'
        return result

    active_cohort.sort(
        key=lambda item: (
            item['hba1c_visits'][0].procedure_date,
            item['hba1c_visits'][0].id,
        )
    )

    for patient in active_cohort:
        visits = patient['hba1c_visits']
        baseline = visits[0]
        baseline_date = baseline.procedure_date
        baseline_hba1c = float(baseline.hba1c_percent)
        within_six_months = [
            visit for visit in visits[1:]
            if 0 < (visit.procedure_date - baseline_date).days <= 183
        ]
        # Susulan selepas 183 hari tidak boleh dianggap sebagai kejayaan
        # dalam tempoh Indicator-1; pesakit tersebut dikira overdue.
        followup_visits = within_six_months

        if followup_visits:
            best_followup = min(
                followup_visits,
                key=lambda visit: float(visit.hba1c_percent),
            )
            followup_hba1c = float(best_followup.hba1c_percent)
            reduction = round(baseline_hba1c - followup_hba1c, 2)
            achieved = (reduction >= 1.0) or (followup_hba1c <= 7.0)
            result['evaluated_cohort'].append({
                'patient_ref': patient['patient_ref'],
                'baseline_date': baseline_date.strftime('%d %b %Y'),
                'baseline_hba1c': baseline_hba1c,
                'followup_date': best_followup.procedure_date.strftime('%d %b %Y'),
                'followup_hba1c': followup_hba1c,
                'reduction': reduction,
                'days_diff': (best_followup.procedure_date - baseline_date).days,
                'achieved': achieved,
                'is_failure': not achieved,
                'status_label': (
                    f'Achieved (-{reduction:.1f}%)' if reduction >= 1.0
                    else (
                        'Target <=7.0% Reached'
                        if followup_hba1c <= 7.0
                        else f'Not Met ({reduction:+.1f}%)'
                    )
                ),
            })
        else:
            days_elapsed = (as_of - baseline_date).days
            if days_elapsed > 183:
                result['evaluated_cohort'].append({
                    'patient_ref': patient['patient_ref'],
                    'baseline_date': baseline_date.strftime('%d %b %Y'),
                    'baseline_hba1c': baseline_hba1c,
                    'followup_date': 'Overdue (>6 Mo)',
                    'followup_hba1c': None,
                    'reduction': 0.0,
                    'days_diff': days_elapsed,
                    'achieved': False,
                    'is_failure': True,
                    'status_label': 'Overdue (>183 Days No 6M HbA1c)',
                })
            else:
                result['pending_cohort'].append({
                    'patient_ref': patient['patient_ref'],
                    'baseline_date': baseline_date.strftime('%d %b %Y'),
                    'baseline_hba1c': baseline_hba1c,
                    'days_remaining': max(0, 183 - days_elapsed),
                })

    evaluated = result['evaluated_cohort']
    numerator = sum(1 for item in evaluated if item['achieved'])
    denominator = len(evaluated)
    result.update({
        'numerator': numerator,
        'denominator': denominator,
    })

    if denominator:
        score = _clamp_score((numerator / denominator) * 100.0)
        result.update({
            'score': score,
            'status': _score_status(score),
            'reason': None,
        })
    elif result['pending_cohort']:
        result.update({
            'status': 'PENDING_VERIFICATION',
            'reason': 'Kohort HbA1c masih menunggu susulan 6 bulan.',
        })
    else:
        result.update({
            'status': 'NO_DATA',
            'reason': 'Tiada pesakit dalam kohort yang boleh dinilai.',
        })
    return result


def get_module_scores(user, as_of=None):
    """
    Kira skor 0-100 untuk 10 modul berdasarkan data DB sebenar.

    ``score=None`` bermaksud tiada ukuran sah. ``score=0.0`` bermaksud
    rekod wujud tetapi nilai agregat benar-benar sifar.
    """
    if as_of is None:
        as_of = timezone.localdate()
    elif isinstance(as_of, datetime):
        if timezone.is_naive(as_of):
            as_of = timezone.make_aware(as_of)
        as_of = timezone.localtime(as_of).date()
    elif not isinstance(as_of, date):
        raise TypeError('as_of mesti date, datetime atau None')

    modules = []

    # 1. Clinical Delivery
    department = getattr(getattr(user, 'profile', None), 'department', None)
    if department == 'FMS':
        kkm_result = _kkm_hba1c_result(user, as_of=as_of)
        modules.append(_module_result(
            'clinical_delivery', 'Clinical Delivery',
            score=kkm_result['score'],
            n=kkm_result['denominator'],
            denominator=kkm_result['denominator'],
            raw_value=kkm_result['numerator'],
            status=kkm_result['status'],
            reason=kkm_result['reason'],
            has_records=kkm_result['has_records'],
            source='ProcedureLog.hba1c_percent (KKM Indicator-1)',
            target=80,
        ))
    else:
        # Laluan lama dikekalkan untuk jabatan bukan FMS (cth. CTS).
        procedures = ProcedureLog.objects.filter(officer=user)
        has_proc_records = procedures.exists()
        verified_procedures = procedures.filter(
            procedure_date__year=as_of.year,
            is_verified=True,
        )
        verified_count = verified_procedures.count()
        if not has_proc_records:
            modules.append(_module_result(
                'clinical_delivery', 'Clinical Delivery',
                status='NO_DATA', reason='Tiada rekod prosedur.',
                source='ProcedureLog.is_successful (verified)',
            ))
        elif not verified_count:
            modules.append(_module_result(
                'clinical_delivery', 'Clinical Delivery',
                n=0, denominator=0, status='PENDING_VERIFICATION',
                reason='Tiada prosedur verified bagi tahun semasa.',
                has_records=True,
                source='ProcedureLog.is_successful (verified)',
            ))
        else:
            successful_count = verified_procedures.filter(is_successful=True).count()
            score = _clamp_score((successful_count / verified_count) * 100.0)
            modules.append(_module_result(
                'clinical_delivery', 'Clinical Delivery',
                score=score, n=verified_count, denominator=verified_count,
                raw_value=successful_count,
                status=_score_status(score),
                has_records=True,
                source='ProcedureLog.is_successful (verified)',
            ))

    # 2. Documentation
    all_docs = DocumentationLog.objects.filter(officer=user)
    has_doc_records = all_docs.exists()
    completed_rows = all_docs.filter(is_completed=True).values_list(
        'encounter_at', 'completed_at'
    )
    valid_durations = []
    invalid_completed = 0
    for encounter_at, completed_at in completed_rows:
        if encounter_at is None:
            invalid_completed += 1
            continue
        if _local_date(encounter_at).year != as_of.year:
            continue
        if completed_at is None or completed_at < encounter_at:
            invalid_completed += 1
            continue
        valid_durations.append(completed_at - encounter_at)

    if valid_durations:
        on_time_count = sum(
            1 for duration in valid_durations
            if duration <= timedelta(hours=4)
        )
        score = _clamp_score((on_time_count / len(valid_durations)) * 100.0)
        modules.append(_module_result(
            'documentation', 'Documentation',
            score=score,
            n=len(valid_durations),
            denominator=len(valid_durations),
            raw_value=on_time_count,
            status=_score_status(score),
            has_records=True,
            source='DocumentationLog.encounter_at/completed_at',
        ))
    else:
        pending_count = all_docs.filter(is_completed=False).count()
        if invalid_completed:
            status = 'INVALID_DATA'
            reason = 'Timestamp encounter/completed tidak sah.'
        elif pending_count:
            status = 'PENDING_VERIFICATION'
            reason = 'Tiada nota completed; rekod masih pending.'
        else:
            status = 'NO_DATA'
            reason = 'Tiada nota completed bagi tahun semasa.'
        modules.append(_module_result(
            'documentation', 'Documentation',
            status=status, reason=reason,
            has_records=has_doc_records,
            source='DocumentationLog.encounter_at/completed_at',
        ))

    # 3. Knowledge & Competency (CPD)
    cpd_trajectory = build_cpd_trajectory(user, as_of=as_of)
    cpd_score = cpd_trajectory['score']['final']
    if cpd_score is None:
        cpd_status = (
            'PENDING_VERIFICATION'
            if cpd_trajectory['status'] == 'PENDING_VERIFICATION'
            else 'NO_DATA'
        )
    else:
        cpd_status = _score_status(cpd_score)
    modules.append(_module_result(
        'cpd', 'Knowledge & Competency (CPD)',
        score=cpd_score,
        n=cpd_trajectory['points']['approved_count'],
        denominator=CPD_TARGET_POINTS,
        raw_value=cpd_trajectory['points']['approved'],
        status=cpd_status,
        reason=cpd_trajectory['reason'],
        has_records=cpd_trajectory['has_any_claims'],
        source='CPDClaim.points + kkm_domain (60/40 composite)',
        target=CPD_TARGET_POINTS,
        breakdown=cpd_trajectory,
    ))


    # 4. Adherence To Policies
    declarations = PolicyDeclaration.objects.filter(officer=user)
    has_policy_records = declarations.exists()
    declaration = declarations.filter(
        year=as_of.year
    ).order_by('-submitted_at', '-id').first()
    if declaration is None:
        modules.append(_module_result(
            'policies', 'Adherence To Policies',
            status='NO_DATA', reason='Tiada deklarasi polisi tahun semasa.',
            has_records=has_policy_records,
            source='PolicyItemResponse.compliance_ratio',
            target=20,
        ))
    else:
        ratios = {}
        response_rows = declaration.responses.filter(
            question_number__gte=1,
            question_number__lte=20,
        ).order_by('id').values_list('question_number', 'compliance_ratio')
        for question_number, ratio in response_rows:
            ratios[question_number] = float(ratio or 0.0)

        if ratios:
            score = _clamp_score((sum(ratios.values()) / 20.0) * 100.0)
            modules.append(_module_result(
                'policies', 'Adherence To Policies',
                score=score,
                n=len(ratios),
                denominator=20,
                raw_value=sum(ratios.values()),
                status=_score_status(score),
                has_records=True,
                source='PolicyItemResponse.compliance_ratio',
                target=20,
            ))
        else:
            domains = [
                declaration.score_domain_a,
                declaration.score_domain_b,
                declaration.score_domain_c,
                declaration.score_domain_d,
                declaration.score_domain_e,
            ]
            score = _clamp_score(sum(domains) / 5.0)
            modules.append(_module_result(
                'policies', 'Adherence To Policies',
                score=score,
                n=5,
                denominator=20,
                raw_value=score,
                status='ZERO' if score == 0 else 'PROVISIONAL',
                reason='Fallback purata 5 domain; tiada item response.',
                is_provisional=True,
                has_records=True,
                source='PolicyDeclaration.score_domain_a..e',
                target=20,
            ))

    # 5. Productivity (EWMA lambda = 0.30)
    ewma_profile = get_productivity_ewma_profile(user, as_of=as_of, window_days=30)
    if not ewma_profile['has_data']:
        modules.append(_module_result(
            'productivity', 'Productivity',
            status='NO_DATA', reason='Tiada log produktiviti 30 hari terakhir.',
            has_records=ewma_profile['has_any_records'],
            source='ProductivityLog.patients_cleared (EWMA λ=0.3)',
            target=38.22,
        ))
    else:
        ewma_latest = ewma_profile['ewma_latest']
        score = _clamp_score(50.0 * ewma_latest / 38.22)
        modules.append(_module_result(
            'productivity', 'Productivity',
            score=score,
            n=ewma_profile['observation_count'],
            denominator=30,
            raw_value=ewma_latest,
            status=_score_status(score),
            has_records=True,
            source='ProductivityLog.patients_cleared (EWMA λ=0.3)',
            target=38.22,
        ))

    # 6. Innovation
    all_innovations = InnovationProject.objects.filter(officer=user)
    has_innovation_records = all_innovations.exists()
    year_innovations = all_innovations.filter(submitted_at__year=as_of.year)
    if not year_innovations.exists():
        modules.append(_module_result(
            'innovation', 'Innovation',
            status='NO_DATA', reason='Tiada projek inovasi tahun semasa.',
            has_records=has_innovation_records,
            source='InnovationProject.final_points',
            target=20,
        ))
    else:
        final_points = (
            year_innovations.aggregate(total=Sum('final_points'))['total'] or 0
        )
        score = _clamp_score((final_points / 20.0) * 100.0)
        unverified = year_innovations.filter(is_verified=False).exists()
        modules.append(_module_result(
            'innovation', 'Innovation',
            score=score,
            n=year_innovations.count(),
            denominator=20,
            raw_value=final_points,
            status='PENDING_VERIFICATION' if unverified else _score_status(score),
            reason='Projek belum diverifikasi.' if unverified else None,
            is_provisional=unverified,
            has_records=True,
            source='InnovationProject.final_points',
            target=20,
        ))


    # 7. Patient Satisfaction (MyPSQ)
    all_mypsq = MyPSQFeedback.objects.filter(doctor=user)
    has_mypsq_records = all_mypsq.exists()
    mypsq_start = as_of - timedelta(days=89)
    mypsq_rows = list(all_mypsq.filter(
        created_at__date__gte=mypsq_start,
        created_at__date__lte=as_of,
    ).values_list(
        'q1_general', 'q2_technical', 'q3_interpersonal',
        'q4_communication', 'q5_financial', 'q6_time', 'q7_accessibility',
    ))
    if not mypsq_rows:
        modules.append(_module_result(
            'mypsq', 'Patient Satisfaction (MyPSQ)',
            status='NO_DATA', reason='Tiada feedback MyPSQ 90 hari terakhir.',
            has_records=has_mypsq_records,
            source='MyPSQFeedback.average_score',
            target=5,
        ))
    else:
        feedback_averages = [sum(row) / 7.0 for row in mypsq_rows]
        average_rating = sum(feedback_averages) / len(feedback_averages)
        score = _clamp_score((average_rating / 5.0) * 100.0)
        modules.append(_module_result(
            'mypsq', 'Patient Satisfaction (MyPSQ)',
            score=score,
            n=len(mypsq_rows),
            denominator=5,
            raw_value=average_rating,
            status=_score_status(score),
            has_records=True,
            source='MyPSQFeedback.average_score',
            target=5,
        ))

    # 8. Stress Management (EI)
    all_reflections = EmotionalReflection.objects.filter(officer=user)
    has_ei_records = all_reflections.exists()
    ei_start = as_of - timedelta(days=364)
    reflection_rows = list(all_reflections.filter(
        record_date__gte=ei_start,
        record_date__lte=as_of,
    ).values_list(
        'self_awareness_score',
        'self_regulation_score',
        'empathy_score',
        'deescalation_score',
    ))
    reflection_composites = []
    partial_ei = False
    for row in reflection_rows:
        available = [value for value in row if value is not None]
        if not available:
            continue
        if len(available) < 4:
            partial_ei = True
        reflection_composites.append(sum(available) / len(available))

    if not reflection_composites:
        modules.append(_module_result(
            'emotional_intelligence', 'Stress Management (EI)',
            status='INVALID_DATA' if reflection_rows else 'NO_DATA',
            reason=(
                'Tiada domain EI yang sah.'
                if reflection_rows else
                'Tiada refleksi EI 365 hari terakhir.'
            ),
            has_records=has_ei_records,
            source='EmotionalReflection domain scores',
            target=100,
        ))
    else:
        average_ei = sum(reflection_composites) / len(reflection_composites)
        score = _clamp_score(average_ei)
        modules.append(_module_result(
            'emotional_intelligence', 'Stress Management (EI)',
            score=score,
            n=len(reflection_rows),
            denominator=4,
            raw_value=average_ei,
            status='PROVISIONAL' if partial_ei else _score_status(score),
            reason=(
                'Sebahagian domain EI tidak lengkap.'
                if partial_ei else None
            ),
            is_provisional=partial_ei,
            has_records=True,
            source='EmotionalReflection domain scores',
            target=100,
        ))


    # 9. Teamwork & Collaboration
    all_kudos = PeerKudos.objects.filter(receiver=user)
    has_teamwork_records = all_kudos.exists()
    teamwork_start = as_of - timedelta(days=364)
    kudos_points = [
        max(0, points or 0)
        for points in all_kudos.filter(
            sent_at__date__gte=teamwork_start,
            sent_at__date__lte=as_of,
        ).values_list('points', flat=True)
    ]
    if not kudos_points:
        modules.append(_module_result(
            'teamwork', 'Teamwork & Collaboration',
            status='NO_DATA', reason='Tiada kudos diterima 365 hari terakhir.',
            has_records=has_teamwork_records,
            source='PeerKudos.points (received)',
            target=30,
        ))
    else:
        total_points = min(30, sum(kudos_points))
        score = _clamp_score((total_points / 30.0) * 100.0)
        modules.append(_module_result(
            'teamwork', 'Teamwork & Collaboration',
            score=score,
            n=len(kudos_points),
            denominator=30,
            raw_value=total_points,
            status=_score_status(score),
            has_records=True,
            source='PeerKudos.points (received)',
            target=30,
        ))

    # 10. Training & Mentorship
    mentor_sessions = MentorshipSession.objects.filter(
        mentor=user,
        participant_role='mentor',
    )
    has_mentorship_records = mentor_sessions.exists()
    year_sessions = mentor_sessions.filter(session_date__year=as_of.year)
    if not year_sessions.exists():
        modules.append(_module_result(
            'mentorship', 'Training & Mentorship',
            status='NO_DATA', reason='Tiada sesi mentor tahun semasa.',
            has_records=has_mentorship_records,
            source='MentorshipSession.duration_hours (verified)',
            target=10,
        ))
    else:
        verified_sessions = year_sessions.filter(is_verified=True)
        if not verified_sessions.exists():
            modules.append(_module_result(
                'mentorship', 'Training & Mentorship',
                status='PENDING_VERIFICATION',
                reason='Sesi mentor belum diverifikasi.',
                has_records=True,
                source='MentorshipSession.duration_hours (verified)',
                target=10,
            ))
        else:
            verified_hours = (
                verified_sessions.aggregate(
                    total=Sum('duration_hours')
                )['total'] or 0
            )
            score = _clamp_score((verified_hours / 10.0) * 100.0)
            modules.append(_module_result(
                'mentorship', 'Training & Mentorship',
                score=score,
                n=verified_sessions.count(),
                denominator=10,
                raw_value=verified_hours,
                status=_score_status(score),
                has_records=True,
                source='MentorshipSession.duration_hours (verified)',
                target=10,
            ))

    by_key = {module['key']: module for module in modules}
    return {
        'modules': modules,
        'by_key': by_key,
        'recorded_count': sum(
            1 for module in modules if module['has_records']
        ),
        'scorable_count': sum(
            1 for module in modules if module['score'] is not None
        ),
    }


MYPSQ_DIMENSION_META = (
    ('q1', 'q1_general', 'Kepuasan Am (General Satisfaction)'),
    ('q2', 'q2_technical', 'Kualiti Teknikal (Technical Quality)'),
    ('q3', 'q3_interpersonal', 'Sikap Interpersonal (Interpersonal Manner)'),
    ('q4', 'q4_communication', 'Kejelasan Komunikasi (Communication)'),
    ('q5', 'q5_financial', 'Aspek Kewangan (Financial Value)'),
    ('q6', 'q6_time', 'Masa Bersama Pesakit (Time Spent)'),
    ('q7', 'q7_accessibility', 'Akses & Masa Menunggu (Accessibility)'),
)


def _shift_month_start(anchor_date, delta_months):
    """Pulangkan tarikh hari pertama bulan selepas anjakan delta_months."""
    total_months = (anchor_date.year * 12 + (anchor_date.month - 1)) + delta_months
    year = total_months // 12
    month = (total_months % 12) + 1
    return date(year, month, 1)


def get_mypsq_rolling_profile(user, as_of=None, window_days=90, target_pct=80.0):
    """
    Kira purata bergerak 3 bulan (90 hari) MyPSQ, pecahan 7 dimensi rasmi,
    siri trend bulanan berbanding sasaran 80% (4.0/5.0), dan mesej coaching.
    """
    if as_of is None:
        as_of = timezone.localdate()
    elif isinstance(as_of, datetime):
        if timezone.is_naive(as_of):
            as_of = timezone.make_aware(as_of)
        as_of = timezone.localtime(as_of).date()

    empty_dim_scores = {code: 0.0 for code, _field, _label in MYPSQ_DIMENSION_META}
    empty_dim_list = [
        {'code': code, 'label': label, 'score': None, 'pct': 0}
        for code, _field, label in MYPSQ_DIMENSION_META
    ]

    if user is None:
        return {
            'has_data': False,
            'has_any_records': False,
            'avg_score': None,
            'score_pct': None,
            'response_count': 0,
            'all_time_count': 0,
            'star_count': 0,
            'stars_display': '☆☆☆☆☆',
            'dim_scores': empty_dim_scores,
            'dimensions': empty_dim_list,
            'strongest_dimension': None,
            'weakest_dimension': None,
            'monthly_series': [],
            'target_pct': target_pct,
            'status': 'NO_DATA',
            'badge_label': 'No Data',
            'badge_bg': '#f1f5f9',
            'badge_color': '#64748b',
            'badge_border': '#cbd5e1',
            'card_bg': '#f8fafc',
            'card_border': '#cbd5e1',
            'coaching_msg': 'Tiada maklum balas pesakit MyPSQ direkodkan dalam 90 hari terakhir.',
        }

    all_qs = MyPSQFeedback.objects.filter(doctor=user)
    all_time_count = all_qs.count()
    start_date = as_of - timedelta(days=max(1, window_days) - 1)
    recent_feedbacks = list(
        all_qs.filter(
            created_at__date__gte=start_date,
            created_at__date__lte=as_of,
        ).order_by('-created_at', '-id')
    )

    # Bina siri trend 3 bulan terakhir untuk graf garis HOD
    monthly_series = []
    for offset in (-2, -1, 0):
        m_start = _shift_month_start(as_of, offset)
        m_next = _shift_month_start(as_of, offset + 1)
        m_items = [
            fb for fb in recent_feedbacks
            if m_start <= _local_date(fb.created_at) < m_next
        ]
        if m_items:
            m_avg = sum(fb.average_score for fb in m_items) / len(m_items)
            m_pct = round((m_avg / 5.0) * 100.0, 1)
        else:
            m_avg = None
            m_pct = None
        monthly_series.append({
            'month_label': m_start.strftime('%b %Y'),
            'avg_score': round(m_avg, 2) if m_avg is not None else None,
            'score_pct': m_pct,
            'count': len(m_items),
        })

    if not recent_feedbacks:
        return {
            'has_data': False,
            'has_any_records': all_time_count > 0,
            'avg_score': None,
            'score_pct': None,
            'response_count': 0,
            'all_time_count': all_time_count,
            'star_count': 0,
            'stars_display': '☆☆☆☆☆',
            'dim_scores': empty_dim_scores,
            'dimensions': empty_dim_list,
            'strongest_dimension': None,
            'weakest_dimension': None,
            'monthly_series': monthly_series,
            'target_pct': target_pct,
            'status': 'NO_DATA',
            'badge_label': 'No Data',
            'badge_bg': '#f1f5f9',
            'badge_color': '#64748b',
            'badge_border': '#cbd5e1',
            'card_bg': '#f8fafc',
            'card_border': '#cbd5e1',
            'coaching_msg': (
                'Belum ada penilaian pesakit MyPSQ dalam 90 hari terakhir. '
                'Kongsi kod QR peribadi anda di kaunter/bilik konsultasi selepas rawatan.'
            ),
        }

    n = len(recent_feedbacks)
    avg_score = round(sum(fb.average_score for fb in recent_feedbacks) / n, 1)
    score_pct = round((avg_score / 5.0) * 100.0, 1)
    star_count = max(1, min(5, int(round(avg_score))))
    stars_display = ('★' * star_count) + ('☆' * (5 - star_count))

    dim_scores = {}
    dimensions = []
    for code, field_name, label in MYPSQ_DIMENSION_META:
        d_avg = round(sum(getattr(fb, field_name) for fb in recent_feedbacks) / n, 1)
        d_pct = int(round((d_avg / 5.0) * 100.0))
        dim_scores[code] = d_avg
        dimensions.append({
            'code': code,
            'label': label,
            'score': d_avg,
            'pct': d_pct,
        })

    strongest = max(dimensions, key=lambda item: item['score'])
    weakest = min(dimensions, key=lambda item: item['score'])

    if score_pct >= 85.0:
        status = 'OPTIMAL'
        badge_label = f'Cemerlang ({avg_score:.1f}/5.0)'
        badge_bg, badge_color, badge_border = '#dcfce7', '#16a34a', '#bbf7d0'
        card_bg, card_border = '#f0fdf4', '#bbf7d0'
        coaching_msg = (
            f"Pesakit sangat berpuas hati, terutamanya dalam aspek {strongest['label'].split(' (')[0]} "
            f"({strongest['score']:.1f}/5.0). Teruskan layanan empati dan mesra pesakit!"
        )
    elif score_pct >= target_pct:
        status = 'GOOD'
        badge_label = f'Melepasi Sasaran ({avg_score:.1f}/5.0)'
        badge_bg, badge_color, badge_border = '#f3edff', '#6d3ec4', '#d8c7f4'
        card_bg, card_border = '#faf8ff', '#d8c7f4'
        coaching_msg = (
            f"Purata 3 bulan anda ({score_pct:.0f}%) melepasi sasaran 80%. "
            f"Kekuatan utama: {strongest['label'].split(' (')[0]} ({strongest['score']:.1f}/5.0)."
        )
    else:
        status = 'NEEDS_ATTENTION'
        badge_label = f'Perlu Perhatian ({avg_score:.1f}/5.0)'
        badge_bg, badge_color, badge_border = '#fef3c7', '#d97706', '#fde68a'
        card_bg, card_border = '#fffdf5', '#fde68a'
        coaching_msg = (
            f"Purata 3 bulan semasa ({score_pct:.0f}%) berada di bawah sasaran 80%. "
            f"Fokus penambahbaikan pada {weakest['label'].split(' (')[0]} ({weakest['score']:.1f}/5.0)."
        )

    return {
        'has_data': True,
        'has_any_records': True,
        'avg_score': avg_score,
        'score_pct': score_pct,
        'response_count': n,
        'all_time_count': all_time_count,
        'star_count': star_count,
        'stars_display': stars_display,
        'dim_scores': dim_scores,
        'dimensions': dimensions,
        'strongest_dimension': strongest,
        'weakest_dimension': weakest,
        'monthly_series': monthly_series,
        'target_pct': target_pct,
        'status': status,
        'badge_label': badge_label,
        'badge_bg': badge_bg,
        'badge_color': badge_color,
        'badge_border': badge_border,
        'card_bg': card_bg,
        'card_border': card_border,
        'coaching_msg': coaching_msg,
    }


EI_DOMAIN_LABELS = (
    ('self_awareness_score', 'Self-Awareness'),
    ('self_regulation_score', 'Self-Regulation'),
    ('empathy_score', 'Empathy'),
    ('deescalation_score', 'De-escalation'),
)


def get_ei_wellness_profile(user, as_of=None, window_days=365, lam=0.30):
    """
    Kira analisis trend EWMA (lambda=0.30) bagi skor Refleksi Emosi (EI) 4 domain.

    PRIVASI: Fungsi ini TIDAK sekali-kali memulangkan ``reflection_notes``,
    ``trigger_event``, atau ``coping_strategy`` supaya nota peribadi staf
    tidak terdedah kepada paparan HOD.
    """
    if as_of is None:
        as_of = timezone.localdate()
    elif isinstance(as_of, datetime):
        if timezone.is_naive(as_of):
            as_of = timezone.make_aware(as_of)
        as_of = timezone.localtime(as_of).date()

    empty_domains = [
        {'key': field, 'label': label, 'score': None, 'display': 'No Data'}
        for field, label in EI_DOMAIN_LABELS
    ]

    if user is None:
        return {
            'has_data': False,
            'has_any_records': False,
            'status': 'NO_DATA',
            'badge_label': 'No Data',
            'badge_bg': '#f1f5f9',
            'badge_color': '#64748b',
            'badge_border': '#cbd5e1',
            'card_bg': '#f8fafc',
            'card_border': '#cbd5e1',
            'ewma_latest': None,
            'composite_avg': None,
            'delta': 0.0,
            'session_count': 0,
            'dates': [],
            'raw_series': [],
            'ewma_series': [],
            'domains': empty_domains,
            'domain_values': [0, 0, 0, 0],
            'coaching_msg': 'Tiada log refleksi EI direkodkan dalam 365 hari terakhir.',
            'surveillance_note': 'Pegawai ini belum mempunyai rekod refleksi kesejahteraan emosi.',
        }

    all_qs = EmotionalReflection.objects.filter(officer=user)
    has_any_records = all_qs.exists()
    start_date = as_of - timedelta(days=max(1, window_days) - 1)
    reflections = list(
        all_qs.filter(
            record_date__gte=start_date,
            record_date__lte=as_of,
        ).order_by('record_date', 'id')
    )

    dates = []
    raw_series = []
    domain_buckets = {field: [] for field, _label in EI_DOMAIN_LABELS}

    for ref in reflections:
        avail = []
        for field, _label in EI_DOMAIN_LABELS:
            val = getattr(ref, field, None)
            if val is not None:
                fval = float(val)
                avail.append(fval)
                domain_buckets[field].append(fval)
        if avail:
            dates.append(ref.record_date)
            raw_series.append(round(sum(avail) / len(avail), 1))

    if not raw_series:
        return {
            'has_data': False,
            'has_any_records': has_any_records,
            'status': 'NO_DATA',
            'badge_label': 'No Data',
            'badge_bg': '#f1f5f9',
            'badge_color': '#64748b',
            'badge_border': '#cbd5e1',
            'card_bg': '#f8fafc',
            'card_border': '#cbd5e1',
            'ewma_latest': None,
            'composite_avg': None,
            'delta': 0.0,
            'session_count': 0,
            'dates': [],
            'raw_series': [],
            'ewma_series': [],
            'domains': empty_domains,
            'domain_values': [0, 0, 0, 0],
            'coaching_msg': (
                'Belum ada rekod Wellness Check-In EI dalam 365 hari terakhir. '
                'Luangkan 1 minit merekod tahap tenaga dan emosi anda selepas syif.'
            ),
            'surveillance_note': 'Tiada data trend EI bagi pegawai ini dalam tempoh semasa.',
        }

    ewma_series = []
    z_curr = raw_series[0]
    for idx, val in enumerate(raw_series):
        if idx == 0:
            z_curr = val
        else:
            z_curr = (lam * val) + ((1.0 - lam) * z_curr)
        ewma_series.append(round(z_curr, 1))

    ewma_latest = ewma_series[-1]
    composite_avg = round(sum(raw_series) / len(raw_series), 1)
    delta = round(ewma_latest - ewma_series[0], 1) if len(ewma_series) > 1 else 0.0

    domains = []
    domain_values = []
    for field, label in EI_DOMAIN_LABELS:
        vals = domain_buckets[field]
        if vals:
            avg_d = round(sum(vals) / len(vals), 1)
            domains.append({
                'key': field,
                'label': label,
                'score': avg_d,
                'display': f'{avg_d:.0f}%',
            })
            domain_values.append(avg_d)
        else:
            domains.append({
                'key': field,
                'label': label,
                'score': None,
                'display': 'No Data',
            })
            domain_values.append(0)

    weakest_dom = min(
        (d for d in domains if d['score'] is not None),
        key=lambda item: item['score'],
    )

    if ewma_latest < 60.0:
        status = 'ALERT'
        badge_label = 'Perlu Rehat • High Stress'
        badge_bg, badge_color, badge_border = '#fee2e2', '#dc2626', '#fecaca'
        card_bg, card_border = '#fffafa', '#fecaca'
        coaching_msg = (
            f'Trend EI EWMA semasa ({ewma_latest:.1f}%) menunjukkan tahap tekanan tinggi. '
            'Sila ambil masa untuk self-care, rehat mencukupi, atau berbincang dengan mentor/HOD.'
        )
        surveillance_note = (
            f'Isyarat Tekanan Tinggi: Trend EWMA EI berada pada {ewma_latest:.1f}% (<60%). '
            'Pertimbangkan sesi sokongan beban kerja (nota refleksi kekal sulit).'
        )
    elif ewma_latest < 75.0 or delta < -10.0:
        status = 'WATCH'
        badge_label = 'Perhatian Awal • Calibration'
        badge_bg, badge_color, badge_border = '#fef3c7', '#d97706', '#fde68a'
        card_bg, card_border = '#fffdf5', '#fde68a'
        coaching_msg = (
            f'Tahap stres memerlukan perhatian ringan (EWMA {ewma_latest:.1f}%). '
            f'Fokus pengukuhan pada domain {weakest_dom["label"]} ({weakest_dom["display"]}).'
        )
        surveillance_note = (
            f'Pemerhatian Awal: Trend EWMA EI pada {ewma_latest:.1f}% '
            f'(domain terendah: {weakest_dom["label"]} {weakest_dom["display"]}).'
        )
    else:
        status = 'OPTIMAL'
        badge_label = 'Stabil • Resilient'
        badge_bg, badge_color, badge_border = '#dcfce7', '#16a34a', '#bbf7d0'
        card_bg, card_border = '#f0fdf4', '#bbf7d0'
        coaching_msg = (
            f'Tahap kesejahteraan emosi anda stabil minggu ini (EWMA {ewma_latest:.1f}%). '
            'Teruskan amalan pengurusan stres yang sihat!'
        )
        surveillance_note = (
            f'Kesejahteraan Emosi Stabil: Trend EWMA EI berada pada {ewma_latest:.1f}% '
            f'merentas {len(raw_series)} sesi refleksi.'
        )

    return {
        'has_data': True,
        'has_any_records': True,
        'status': status,
        'badge_label': badge_label,
        'badge_bg': badge_bg,
        'badge_color': badge_color,
        'badge_border': badge_border,
        'card_bg': card_bg,
        'card_border': card_border,
        'ewma_latest': ewma_latest,
        'composite_avg': composite_avg,
        'delta': delta,
        'session_count': len(raw_series),
        'dates': dates,
        'raw_series': raw_series,
        'ewma_series': ewma_series,
        'domains': domains,
        'domain_values': domain_values,
        'coaching_msg': coaching_msg,
        'surveillance_note': surveillance_note,
    }


KUDOS_POINT_CEILING = 30

KUDOS_CATEGORY_LIST = (
    'Consultation based on subspecialty/AOI',
    'Accepting referrals from other FMS',
    'Covering inter / intrafacility FMS',
    'Non-clinical consultation',
    'Research /Innovation related support',
    'Sharing resources – drugs, staff, lab',
    'Clinical-related activities',
    'General clinical consultation',
)


def get_teamwork_recognition_profile(
    user,
    as_of=None,
    window_days=365,
    target_points=30.0,
):
    """
    Kira Indeks Pengiktirafan Kerja Berpasukan (Rolling 12-Month Recognition Index)
    berasaskan mata Kudos diterima dalam 365 hari terakhir berbanding sasaran 30 mata.
    """
    if as_of is None:
        as_of = timezone.localdate()
    elif isinstance(as_of, datetime):
        if timezone.is_naive(as_of):
            as_of = timezone.make_aware(as_of)
        as_of = timezone.localtime(as_of).date()

    empty_categories = [
        {'label': cat, 'points': 0, 'count': 0}
        for cat in KUDOS_CATEGORY_LIST
    ]

    if user is None:
        return {
            'has_data': False,
            'has_any_records': False,
            'received_points': 0,
            'given_points': 0,
            'received_count': 0,
            'given_count': 0,
            'unique_peers': 0,
            'target_points': int(target_points),
            'attainment_pct': 0,
            'category_breakdown': empty_categories,
            'status': 'NO_DATA',
            'badge_label': 'No Data',
            'badge_bg': '#f1f5f9',
            'badge_color': '#64748b',
            'badge_border': '#cbd5e1',
            'card_bg': '#f8fafc',
            'card_border': '#cbd5e1',
            'coaching_msg': 'Tiada rekod Peer Kudos dalam 365 hari terakhir.',
        }

    start_date = as_of - timedelta(days=max(1, window_days) - 1)
    all_received = PeerKudos.objects.filter(receiver=user)
    has_any_records = (
        all_received.exists()
        or PeerKudos.objects.filter(sender=user).exists()
    )

    received_qs = list(
        all_received.filter(
            sent_at__date__gte=start_date,
            sent_at__date__lte=as_of,
        ).select_related('sender').order_by('-sent_at', '-id')
    )
    given_qs = list(
        PeerKudos.objects.filter(
            sender=user,
            sent_at__date__gte=start_date,
            sent_at__date__lte=as_of,
        ).values_list('points', 'receiver_id')
    )

    raw_received_points = sum(max(0, k.points or 0) for k in received_qs)
    received_points = min(int(target_points), raw_received_points)
    given_points = sum(max(0, pts or 0) for pts, _rid in given_qs)
    unique_peers = len(
        {k.sender_id for k in received_qs}
        | {rid for _pts, rid in given_qs}
    )

    cat_map = {cat: {'label': cat, 'points': 0, 'count': 0} for cat in KUDOS_CATEGORY_LIST}
    other_bucket = {'label': 'Peer Endorsement', 'points': 0, 'count': 0}

    for k in received_qs:
        pts = max(0, k.points or 0)
        cat_name = (k.category or '').strip()
        if cat_name in cat_map:
            cat_map[cat_name]['points'] += pts
            cat_map[cat_name]['count'] += 1
        else:
            other_bucket['points'] += pts
            other_bucket['count'] += 1

    category_breakdown = list(cat_map.values())
    if other_bucket['count'] > 0:
        category_breakdown.append(other_bucket)

    if not received_qs:
        return {
            'has_data': False,
            'has_any_records': has_any_records,
            'received_points': 0,
            'given_points': given_points,
            'received_count': 0,
            'given_count': len(given_qs),
            'unique_peers': unique_peers,
            'target_points': int(target_points),
            'attainment_pct': 0,
            'category_breakdown': category_breakdown,
            'status': 'NO_DATA',
            'badge_label': 'No Data',
            'badge_bg': '#f1f5f9',
            'badge_color': '#64748b',
            'badge_border': '#cbd5e1',
            'card_bg': '#f8fafc',
            'card_border': '#cbd5e1',
            'coaching_msg': (
                'Belum ada Kudos diterima dalam 365 hari terakhir. '
                'Mulakan budaya penghargaan dengan menghantar Kudos kepada rakan sekerja!'
            ),
        }

    attainment_pct = min(100, int(round((received_points / float(target_points)) * 100.0)))
    top_cat = max(category_breakdown, key=lambda item: item['points'])

    if received_points >= target_points:
        status = 'TARGET_MET'
        badge_label = f'High Collaborator ({received_points}/{int(target_points)} Pts)'
        badge_bg, badge_color, badge_border = '#dcfce7', '#16a34a', '#bbf7d0'
        card_bg, card_border = '#f0fdf4', '#bbf7d0'
        coaching_msg = (
            f'Cemerlang! Anda menerima {received_points} mata Kudos daripada rakan sekerja '
            f'(kekuatan utama: {top_cat["label"]}). Teruskan semangat kerja berpasukan!'
        )
    else:
        remaining = max(0, int(target_points) - received_points)
        status = 'IN_PROGRESS'
        badge_label = f'Active ({received_points}/{int(target_points)} Pts)'
        badge_bg, badge_color, badge_border = '#f3edff', '#6d3ec4', '#d8c7f4'
        card_bg, card_border = '#faf8ff', '#d8c7f4'
        coaching_msg = (
            f'Anda telah mengumpul {received_points}/{int(target_points)} mata Kudos '
            f'(baki {remaining} mata). Sumbangan paling menyerlah: {top_cat["label"]}.'
        )

    return {
        'has_data': True,
        'has_any_records': True,
        'received_points': received_points,
        'given_points': given_points,
        'received_count': len(received_qs),
        'given_count': len(given_qs),
        'unique_peers': unique_peers,
        'target_points': int(target_points),
        'attainment_pct': attainment_pct,
        'category_breakdown': category_breakdown,
        'status': status,
        'badge_label': badge_label,
        'badge_bg': badge_bg,
        'badge_color': badge_color,
        'badge_border': badge_border,
        'card_bg': card_bg,
        'card_border': card_border,
        'coaching_msg': coaching_msg,
    }


def build_department_kudos_leaderboard(
    staff_users,
    selected_user=None,
    as_of=None,
    window_days=365,
    target_points=30.0,
):
    """
    Bina papan pendahulu (Leaderboard) Kudos 12 bulan terakhir bagi staf sebenar
    dalam skop jabatan/klinik HOD.
    """
    if as_of is None:
        as_of = timezone.localdate()
    elif isinstance(as_of, datetime):
        if timezone.is_naive(as_of):
            as_of = timezone.make_aware(as_of)
        as_of = timezone.localtime(as_of).date()

    staff_list = list(staff_users or [])
    if not staff_list:
        return []

    start_date = as_of - timedelta(days=max(1, window_days) - 1)
    staff_ids = [u.id for u in staff_list]

    received_rows = PeerKudos.objects.filter(
        receiver_id__in=staff_ids,
        sent_at__date__gte=start_date,
        sent_at__date__lte=as_of,
    ).values_list('receiver_id', 'points')

    given_rows = PeerKudos.objects.filter(
        sender_id__in=staff_ids,
        sent_at__date__gte=start_date,
        sent_at__date__lte=as_of,
    ).values_list('sender_id', 'points')

    rec_pts_map = {uid: 0 for uid in staff_ids}
    rec_cnt_map = {uid: 0 for uid in staff_ids}
    for uid, pts in received_rows:
        rec_pts_map[uid] = rec_pts_map.get(uid, 0) + max(0, pts or 0)
        rec_cnt_map[uid] = rec_cnt_map.get(uid, 0) + 1

    giv_pts_map = {uid: 0 for uid in staff_ids}
    for uid, pts in given_rows:
        giv_pts_map[uid] = giv_pts_map.get(uid, 0) + max(0, pts or 0)

    board = []
    for u in staff_list:
        full_name = f"{u.first_name} {u.last_name}".strip()
        disp_name = f"Dr. {full_name}" if full_name else f"Dr. {u.username}"
        r_pts = min(int(target_points), rec_pts_map.get(u.id, 0))
        g_pts = giv_pts_map.get(u.id, 0)
        board.append({
            'user_id': u.id,
            'username': u.username,
            'name': disp_name,
            'received_points': r_pts,
            'received_count': rec_cnt_map.get(u.id, 0),
            'given_points': g_pts,
            'attainment_pct': min(100, int(round((r_pts / float(target_points)) * 100.0))),
            'is_selected': bool(selected_user and u.id == selected_user.id),
        })

    board.sort(
        key=lambda item: (
            -item['received_points'],
            -item['received_count'],
            -item['given_points'],
            item['name'].casefold(),
        )
    )
    for idx, item in enumerate(board, start=1):
        item['rank'] = idx

    return board


FMS_MENTORSHIP_ACTIVITIES = (
    ('Post-grad students', 'By individual'),
    ('SWE', 'By individual'),
    ('Medical students', 'Based on group (KIV)'),
    ('Attachment doctors', 'By individual'),
    ('Credentialing & Privileging', 'By individual'),
    ('Others', 'Optional / Custom'),
)


def get_mentorship_trajectory_profile(user, as_of=None, target_hours=10.0):
    """
    Kira profil Trajectory Tracking Training & Mentorship (Jam Disahkan / 10 Jam)
    serta pecahan aktiviti pelatih FMS (By individual vs Group KIV).
    """
    if as_of is None:
        as_of = timezone.localdate()
    elif isinstance(as_of, datetime):
        if timezone.is_naive(as_of):
            as_of = timezone.make_aware(as_of)
        as_of = timezone.localtime(as_of).date()

    empty_activities = [
        {'activity': act, 'tracking_mode': mode, 'hours': 0.0, 'sessions': 0}
        for act, mode in FMS_MENTORSHIP_ACTIVITIES
    ]

    if user is None:
        return {
            'has_data': False,
            'has_any_records': False,
            'year': as_of.year,
            'target_hours': target_hours,
            'verified_hours': 0.0,
            'pending_hours': 0.0,
            'total_hours': 0.0,
            'remaining_hours': target_hours,
            'verified_pct': 0,
            'pending_pct': 0,
            'total_pct': 0,
            'verified_count': 0,
            'pending_count': 0,
            'total_count': 0,
            'individual_trainees_count': 0,
            'group_kiv_sessions_count': 0,
            'activity_breakdown': empty_activities,
            'status': 'NO_DATA',
            'badge_label': 'No Data',
            'badge_bg': '#f1f5f9',
            'badge_color': '#64748b',
            'badge_border': '#cbd5e1',
            'card_bg': '#f8fafc',
            'card_border': '#cbd5e1',
            'coaching_msg': 'Tiada sesi bimbingan (mentor) direkodkan untuk tahun semasa.',
        }

    all_mentor_qs = MentorshipSession.objects.filter(
        mentor=user,
        participant_role='mentor',
    )
    has_any_records = all_mentor_qs.exists()
    year_sessions = list(
        all_mentor_qs.filter(session_date__year=as_of.year).order_by('-session_date', '-id')
    )

    if not year_sessions:
        return {
            'has_data': False,
            'has_any_records': has_any_records,
            'year': as_of.year,
            'target_hours': target_hours,
            'verified_hours': 0.0,
            'pending_hours': 0.0,
            'total_hours': 0.0,
            'remaining_hours': target_hours,
            'verified_pct': 0,
            'pending_pct': 0,
            'total_pct': 0,
            'verified_count': 0,
            'pending_count': 0,
            'total_count': 0,
            'individual_trainees_count': 0,
            'group_kiv_sessions_count': 0,
            'activity_breakdown': empty_activities,
            'status': 'NO_DATA',
            'badge_label': 'No Data',
            'badge_bg': '#f1f5f9',
            'badge_color': '#64748b',
            'badge_border': '#cbd5e1',
            'card_bg': '#f8fafc',
            'card_border': '#cbd5e1',
            'coaching_msg': (
                f'Belum ada sesi latihan & mentorship tahun {as_of.year}. '
                'Rekod bimbingan Post-grad, SWE, Attachment doctors atau C&P untuk mencapai 10 jam.'
            ),
        }

    act_map = {
        act: {'activity': act, 'tracking_mode': mode, 'hours': 0.0, 'sessions': 0}
        for act, mode in FMS_MENTORSHIP_ACTIVITIES
    }

    verified_hours = 0.0
    pending_hours = 0.0
    verified_count = 0
    pending_count = 0
    individual_trainees = set()
    group_kiv_sessions = 0

    for s in year_sessions:
        hrs = float(s.duration_hours or 0.0)
        topic_clean = (s.topic or '').strip()
        bucket_key = topic_clean if topic_clean in act_map else 'Others'
        act_map[bucket_key]['hours'] = round(act_map[bucket_key]['hours'] + hrs, 1)
        act_map[bucket_key]['sessions'] += 1

        if bucket_key == 'Medical students':
            group_kiv_sessions += 1
        else:
            norm_trainee = ' '.join((s.mentee_name or '').strip().lower().split())
            if norm_trainee:
                individual_trainees.add(norm_trainee)

        if s.is_verified:
            verified_hours += hrs
            verified_count += 1
        else:
            pending_hours += hrs
            pending_count += 1

    verified_hours = round(verified_hours, 1)
    pending_hours = round(pending_hours, 1)
    total_hours = round(verified_hours + pending_hours, 1)
    remaining_hours = round(max(0.0, target_hours - verified_hours), 1)

    verified_pct = min(100, int(round((verified_hours / target_hours) * 100.0)))
    total_pct = min(100, int(round((total_hours / target_hours) * 100.0)))
    pending_pct = max(0, total_pct - verified_pct)

    if verified_hours >= target_hours:
        status = 'TARGET_MET'
        badge_label = f'Target Achieved ({verified_hours:g}/{target_hours:g} Hrs)'
        badge_bg, badge_color, badge_border = '#dcfce7', '#16a34a', '#bbf7d0'
        card_bg, card_border = '#f0fdf4', '#bbf7d0'
        coaching_msg = (
            f'Cemerlang! Anda telah melengkapkan {verified_hours:g} jam bimbingan disahkan '
            f'melibatkan {len(individual_trainees)} pelatih individu.'
        )
    elif pending_count > 0 and verified_hours == 0:
        status = 'PENDING_VERIFICATION'
        badge_label = f'Pending HOD Verification ({pending_hours:g} Hrs)'
        badge_bg, badge_color, badge_border = '#fef3c7', '#d97706', '#fde68a'
        card_bg, card_border = '#fffdf5', '#fde68a'
        coaching_msg = (
            f'{pending_count} sesi bimbingan ({pending_hours:g} jam) telah direkodkan '
            'dan sedang menunggu pengesahan HOD.'
        )
    else:
        status = 'IN_PROGRESS'
        badge_label = (
            f'In Progress ({verified_hours:g}/{target_hours:g} Hrs)'
            if pending_count == 0
            else f'{verified_hours:g}h Verified • {pending_hours:g}h Pending'
        )
        badge_bg, badge_color, badge_border = '#f3edff', '#6d3ec4', '#d8c7f4'
        card_bg, card_border = '#faf8ff', '#d8c7f4'
        coaching_msg = (
            f'Baki {remaining_hours:g} jam disahkan lagi untuk mencapai sasaran {target_hours:g} jam '
            f'({len(individual_trainees)} pelatih individu dibimbing setakat ini).'
        )

    return {
        'has_data': True,
        'has_any_records': True,
        'year': as_of.year,
        'target_hours': target_hours,
        'verified_hours': verified_hours,
        'pending_hours': pending_hours,
        'total_hours': total_hours,
        'remaining_hours': remaining_hours,
        'verified_pct': verified_pct,
        'pending_pct': pending_pct,
        'total_pct': total_pct,
        'verified_count': verified_count,
        'pending_count': pending_count,
        'total_count': len(year_sessions),
        'individual_trainees_count': len(individual_trainees),
        'group_kiv_sessions_count': group_kiv_sessions,
        'activity_breakdown': list(act_map.values()),
        'status': status,
        'badge_label': badge_label,
        'badge_bg': badge_bg,
        'badge_color': badge_color,
        'badge_border': badge_border,
        'card_bg': card_bg,
        'card_border': card_border,
        'coaching_msg': coaching_msg,
    }


INNOVATION_MILESTONE_CATEGORIES = (
    'Research',
    'Clinical Trial',
    'Technology Adoption',
    'Publication',
)


def get_innovation_milestone_profile(user, as_of=None, target_points=20.0):
    """
    Kira profil pencapaian Milestone Inovasi (sasaran 20 mata tahunan) dengan
    membezakan mata yang telah disahkan HOD (Verified) dan menunggu sah (Pending).
    """
    if as_of is None:
        as_of = timezone.localdate()
    elif isinstance(as_of, datetime):
        if timezone.is_naive(as_of):
            as_of = timezone.make_aware(as_of)
        as_of = timezone.localtime(as_of).date()

    empty_breakdown = {
        cat: {'verified': 0.0, 'pending': 0.0, 'total': 0.0, 'count': 0}
        for cat in INNOVATION_MILESTONE_CATEGORIES
    }
    empty_breakdown['General/Uncategorised'] = {
        'verified': 0.0, 'pending': 0.0, 'total': 0.0, 'count': 0
    }

    if user is None:
        return {
            'has_data': False,
            'has_any_records': False,
            'year': as_of.year,
            'target_points': target_points,
            'verified_points': 0.0,
            'pending_points': 0.0,
            'total_points': 0.0,
            'remaining_points': target_points,
            'verified_pct': 0,
            'pending_pct': 0,
            'total_pct': 0,
            'total_count': 0,
            'verified_count': 0,
            'pending_count': 0,
            'status': 'NO_DATA',
            'badge_label': 'No Data',
            'badge_bg': '#f1f5f9',
            'badge_color': '#64748b',
            'badge_border': '#cbd5e1',
            'category_breakdown': empty_breakdown,
            'coaching_msg': 'Tiada projek inovasi direkodkan untuk tahun semasa.',
        }

    all_projects = InnovationProject.objects.filter(officer=user)
    has_any_records = all_projects.exists()
    year_projects = list(
        all_projects.filter(submitted_at__year=as_of.year).order_by('-submitted_at', '-id')
    )

    if not year_projects:
        return {
            'has_data': False,
            'has_any_records': has_any_records,
            'year': as_of.year,
            'target_points': target_points,
            'verified_points': 0.0,
            'pending_points': 0.0,
            'total_points': 0.0,
            'remaining_points': target_points,
            'verified_pct': 0,
            'pending_pct': 0,
            'total_pct': 0,
            'total_count': 0,
            'verified_count': 0,
            'pending_count': 0,
            'status': 'NO_DATA',
            'badge_label': 'No Data',
            'badge_bg': '#f1f5f9',
            'badge_color': '#64748b',
            'badge_border': '#cbd5e1',
            'category_breakdown': empty_breakdown,
            'coaching_msg': (
                f'Tiada projek inovasi tahun {as_of.year}. '
                'Satu laporan kes atau projek CQI boleh menyumbang sehingga 10–40 mata.'
            ),
        }

    breakdown = {
        cat: {'verified': 0.0, 'pending': 0.0, 'total': 0.0, 'count': 0}
        for cat in INNOVATION_MILESTONE_CATEGORIES
    }
    breakdown['General/Uncategorised'] = {
        'verified': 0.0, 'pending': 0.0, 'total': 0.0, 'count': 0
    }

    verified_pts = 0.0
    pending_pts = 0.0
    verified_cnt = 0
    pending_cnt = 0

    for proj in year_projects:
        pts = float(proj.final_points or 0.0)
        raw_type = (proj.innovation_type or proj.category or '').strip()
        if raw_type == 'Technology Adaptation':
            raw_type = 'Technology Adoption'
        bucket_key = (
            raw_type if raw_type in INNOVATION_MILESTONE_CATEGORIES
            else 'General/Uncategorised'
        )
        breakdown[bucket_key]['count'] += 1
        breakdown[bucket_key]['total'] = round(breakdown[bucket_key]['total'] + pts, 1)

        if proj.is_verified:
            verified_pts += pts
            verified_cnt += 1
            breakdown[bucket_key]['verified'] = round(
                breakdown[bucket_key]['verified'] + pts, 1
            )
        else:
            pending_pts += pts
            pending_cnt += 1
            breakdown[bucket_key]['pending'] = round(
                breakdown[bucket_key]['pending'] + pts, 1
            )

    verified_pts = round(verified_pts, 1)
    pending_pts = round(pending_pts, 1)
    total_pts = round(verified_pts + pending_pts, 1)
    remaining_pts = round(max(0.0, target_points - verified_pts), 1)

    verified_pct = min(100, int(round((verified_pts / target_points) * 100.0)))
    total_pct = min(100, int(round((total_pts / target_points) * 100.0)))
    pending_pct = max(0, total_pct - verified_pct)

    if verified_pts >= target_points:
        status = 'TARGET_MET'
        badge_label = 'Milestone Achieved (20/20 Pts)'
        badge_bg, badge_color, badge_border = '#dcfce7', '#16a34a', '#bbf7d0'
        coaching_msg = (
            f'Tahniah! Anda telah mencapai {verified_pts:g} mata inovasi yang disahkan '
            f'(melepasi sasaran {target_points:g} mata tahunan).'
        )
    elif pending_cnt > 0 and total_pts >= target_points:
        status = 'PENDING_VERIFICATION'
        badge_label = f'{pending_cnt} Menunggu Pengesahan HOD'
        badge_bg, badge_color, badge_border = '#fef3c7', '#b45309', '#fde68a'
        coaching_msg = (
            f'Anda mempunyai {total_pts:g} mata terkumpul ({verified_pts:g} disahkan, '
            f'{pending_pts:g} menunggu pengesahan HOD).'
        )
    else:
        status = 'PENDING_VERIFICATION' if pending_cnt > 0 else 'IN_PROGRESS'
        badge_label = (
            f'In Progress ({verified_pts:g}/{target_points:g} Pts)'
            if pending_cnt == 0
            else f'{verified_pts:g} Verified • {pending_pts:g} Pending'
        )
        badge_bg, badge_color, badge_border = '#f3edff', '#6d3ec4', '#d8c7f4'
        coaching_msg = (
            f'Baki {remaining_pts:g} mata lagi diperlukan untuk melengkapkan sasaran '
            f'{target_points:g} mata inovasi tahun {as_of.year}.'
        )

    return {
        'has_data': True,
        'has_any_records': True,
        'year': as_of.year,
        'target_points': target_points,
        'verified_points': verified_pts,
        'pending_points': pending_pts,
        'total_points': total_pts,
        'remaining_points': remaining_pts,
        'verified_pct': verified_pct,
        'pending_pct': pending_pct,
        'total_pct': total_pct,
        'total_count': len(year_projects),
        'verified_count': verified_cnt,
        'pending_count': pending_cnt,
        'status': status,
        'badge_label': badge_label,
        'badge_bg': badge_bg,
        'badge_color': badge_color,
        'badge_border': badge_border,
        'category_breakdown': breakdown,
        'coaching_msg': coaching_msg,
    }


def _compute_productivity_ewma(
    daily_entries,
    lam=0.30,
    target=38.22,
    control_limit_l=2.5,
):
    """
    Kira siri EWMA (lambda=0.30) dan Lower Control Limit (LCL) bagi mengesan
    risiko burnout atau kejatuhan mendadak pelepasan pesakit harian.
    """
    if not daily_entries:
        return {
            'has_data': False,
            'status': 'NO_DATA',
            'badge_label': 'No Data',
            'badge_bg': '#f1f5f9',
            'badge_color': '#64748b',
            'badge_border': '#cbd5e1',
            'card_bg': '#f8fafc',
            'card_border': '#cbd5e1',
            'dates': [],
            'raw_series': [],
            'ewma_series': [],
            'lcl_series': [],
            'ewma_latest': None,
            'lcl_latest': None,
            'avg_patients': None,
            'avg_consult': None,
            'punctuality_avg': None,
            'consult_status': 'Tiada Rekod',
            'consult_is_ok': True,
            'coaching_msg': (
                'Belum ada log produktiviti 30 hari terakhir. '
                'Rekod beban kerja harian anda untuk mengaktifkan pemantauan EWMA.'
            ),
            'surveillance_note': 'Pegawai ini belum mempunyai log produktiviti dalam tempoh pemantauan.',
            'observation_count': 0,
            'target': target,
            'lambda': lam,
        }

    dates = [entry[0] for entry in daily_entries]
    xs = [float(max(0, entry[1] or 0)) for entry in daily_entries]
    consults = [
        float(entry[2]) for entry in daily_entries
        if entry[2] is not None and float(entry[2]) > 0
    ]
    punctualities = [
        float(entry[3]) for entry in daily_entries
        if len(entry) > 3 and entry[3] is not None
    ]

    n = len(xs)
    mean_x = sum(xs) / n
    if n > 1:
        variance = sum((x - mean_x) ** 2 for x in xs) / (n - 1)
        sigma = max(4.0, math.sqrt(variance))
    else:
        sigma = 4.0

    ewma_series = []
    lcl_series = []
    z_curr = xs[0]
    for idx, x_val in enumerate(xs, start=1):
        if idx == 1:
            z_curr = x_val
        else:
            z_curr = (lam * x_val) + ((1.0 - lam) * z_curr)
        ewma_series.append(round(z_curr, 2))

        factor = math.sqrt((lam / (2.0 - lam)) * (1.0 - ((1.0 - lam) ** (2 * idx))))
        lcl_val = max(0.0, target - (control_limit_l * sigma * factor))
        lcl_series.append(round(lcl_val, 2))

    ewma_latest = ewma_series[-1]
    lcl_latest = lcl_series[-1]
    avg_patients = round(mean_x, 1)
    avg_consult = round(sum(consults) / len(consults), 1) if consults else None
    punctuality_avg = round(sum(punctualities) / len(punctualities), 1) if punctualities else None

    half_n = max(1, n // 2)
    baseline_ewma = sum(ewma_series[:half_n]) / half_n

    if avg_consult is None:
        consult_status, consult_is_ok = 'Tiada Rekod Masa', True
    elif avg_consult > 15.0:
        consult_status, consult_is_ok = 'Melebihi 15m (Risiko Keletihan)', False
    elif avg_consult < 8.0:
        consult_status, consult_is_ok = 'Terlalu Singkat (<8m)', False
    else:
        consult_status, consult_is_ok = 'Julat Optimum (10–15m)', True

    is_alert = (
        ewma_latest < lcl_latest
        or ewma_latest < 26.0
        or (avg_consult is not None and avg_consult > 18.0)
    )
    is_watch = (
        ewma_latest < 32.5
        or (n >= 4 and baseline_ewma > 0 and ewma_latest < baseline_ewma * 0.85)
        or (avg_consult is not None and (avg_consult > 15.0 or avg_consult < 8.0))
    )

    if is_alert:
        status = 'ALERT'
        badge_label = 'Alert • Burnout Risk'
        badge_bg, badge_color, badge_border = '#fee2e2', '#dc2626', '#fecaca'
        card_bg, card_border = '#fffafa', '#fecaca'
        coaching_msg = (
            f'EWMA semasa ({ewma_latest:.1f} kes/hari) melepasi had kawalan bawah '
            'atau purata masa konsultasi tinggi. Sila ambil rehat taktikal dan bincang agihan tugas.'
        )
        surveillance_note = (
            f'Amaran Burnout/Drift: EWMA ({ewma_latest:.1f} kes/hari) berada di bawah '
            f'LCL ({lcl_latest:.1f}) atau masa konsultasi melebihi 18 minit.'
        )
    elif is_watch:
        status = 'WATCH'
        badge_label = 'Watch • Early Drift'
        badge_bg, badge_color, badge_border = '#fef3c7', '#d97706', '#fde68a'
        card_bg, card_border = '#fffdf5', '#fde68a'
        coaching_msg = (
            f'Rentak EWMA ({ewma_latest:.1f} kes/hari) atau tempoh konsultasi menunjukkan tanda keletihan awal. '
            'Jaga rentak klinikal dan berehat seketika jika perlu.'
        )
        surveillance_note = (
            f'Pemerhatian Awal (Watch): EWMA ({ewma_latest:.1f} kes/hari) atau purata konsultasi '
            f'({avg_consult if avg_consult is not None else "—"} min) mula menyusut.'
        )
    else:
        status = 'IN_CONTROL'
        badge_label = 'In Control • Optimal'
        badge_bg, badge_color, badge_border = '#dcfce7', '#16a34a', '#bbf7d0'
        card_bg, card_border = '#f0fdf4', '#bbf7d0'
        coaching_msg = (
            f'Prestasi EWMA ({ewma_latest:.1f} kes/hari) stabil dan seimbang. '
            'Teruskan momentum kerja yang sihat!'
        )
        surveillance_note = (
            f'Trajektori EWMA ({ewma_latest:.1f} kes/hari) stabil di atas LCL ({lcl_latest:.1f}) '
            f'dengan masa konsultasi purata {avg_consult if avg_consult is not None else "—"} min/pesakit.'
        )

    return {
        'has_data': True,
        'status': status,
        'badge_label': badge_label,
        'badge_bg': badge_bg,
        'badge_color': badge_color,
        'badge_border': badge_border,
        'card_bg': card_bg,
        'card_border': card_border,
        'dates': dates,
        'raw_series': xs,
        'ewma_series': ewma_series,
        'lcl_series': lcl_series,
        'ewma_latest': ewma_latest,
        'lcl_latest': lcl_latest,
        'avg_patients': avg_patients,
        'avg_consult': avg_consult,
        'punctuality_avg': punctuality_avg,
        'consult_status': consult_status,
        'consult_is_ok': consult_is_ok,
        'coaching_msg': coaching_msg,
        'surveillance_note': surveillance_note,
        'observation_count': n,
        'target': target,
        'lambda': lam,
    }


def get_productivity_ewma_profile(user, as_of=None, window_days=30):
    """Tarik log ProductivityLog pegawai dalam tetingkap hari dan kira profil EWMA."""
    if as_of is None:
        as_of = timezone.localdate()
    elif isinstance(as_of, datetime):
        if timezone.is_naive(as_of):
            as_of = timezone.make_aware(as_of)
        as_of = timezone.localtime(as_of).date()

    if user is None:
        profile = _compute_productivity_ewma([])
        profile['has_any_records'] = False
        return profile

    all_productivity = ProductivityLog.objects.filter(officer=user)
    has_any_records = all_productivity.exists()
    start_date = as_of - timedelta(days=max(1, window_days) - 1)
    rows = all_productivity.filter(
        record_date__gte=start_date,
        record_date__lte=as_of,
    ).order_by('record_date', 'id').values_list(
        'record_date',
        'patients_cleared',
        'avg_consult_minutes',
        'punctuality_rate',
    )

    by_date = {}
    for rec_date, cleared, consult_mins, punc_rate in rows:
        by_date[rec_date] = (
            rec_date,
            max(0, cleared or 0),
            consult_mins,
            punc_rate,
        )

    ordered_entries = [by_date[d] for d in sorted(by_date.keys())]
    profile = _compute_productivity_ewma(ordered_entries)
    profile['has_any_records'] = has_any_records
    return profile


def _compute_productivity_ewma(
    daily_entries,
    lam=0.30,
    target=38.22,
    control_limit_l=2.5,
):
    """
    Kira siri EWMA (lambda=0.30) dan Lower Control Limit (LCL) bagi mengesan
    risiko burnout atau kejatuhan mendadak pelepasan pesakit harian.

    ``daily_entries`` ialah senarai tuple (record_date, patients_cleared, avg_consult_minutes)
    yang telah disusun secara kronologi menaik.
    """
    if not daily_entries:
        return {
            'has_data': False,
            'status': 'NO_DATA',
            'badge_label': 'No Data',
            'badge_bg': '#f1f5f9',
            'badge_color': '#64748b',
            'badge_border': '#cbd5e1',
            'card_bg': '#f8fafc',
            'card_border': '#cbd5e1',
            'dates': [],
            'raw_series': [],
            'ewma_series': [],
            'lcl_series': [],
            'ewma_latest': None,
            'lcl_latest': None,
            'avg_patients': None,
            'avg_consult': None,
            'punctuality_avg': None,
            'consult_status': 'Tiada Rekod',
            'consult_is_ok': True,
            'coaching_msg': (
                'Belum ada log produktiviti 30 hari terakhir. '
                'Rekod beban kerja harian anda untuk mengaktifkan pemantauan EWMA.'
            ),
            'surveillance_note': 'Pegawai ini belum mempunyai log produktiviti dalam tempoh pemantauan.',
            'observation_count': 0,
            'target': target,
            'lambda': lam,
        }

    dates = [entry[0] for entry in daily_entries]
    xs = [float(max(0, entry[1] or 0)) for entry in daily_entries]
    consults = [
        float(entry[2]) for entry in daily_entries
        if entry[2] is not None and float(entry[2]) > 0
    ]
    punctualities = [
        float(entry[3]) for entry in daily_entries
        if len(entry) > 3 and entry[3] is not None
    ]

    n = len(xs)
    mean_x = sum(xs) / n
    if n > 1:
        variance = sum((x - mean_x) ** 2 for x in xs) / (n - 1)
        sigma = max(4.0, math.sqrt(variance))
    else:
        sigma = 4.0

    ewma_series = []
    lcl_series = []
    z_curr = xs[0]
    for idx, x_val in enumerate(xs, start=1):
        if idx == 1:
            z_curr = x_val
        else:
            z_curr = (lam * x_val) + ((1.0 - lam) * z_curr)
        ewma_series.append(round(z_curr, 2))

        factor = math.sqrt((lam / (2.0 - lam)) * (1.0 - ((1.0 - lam) ** (2 * idx))))
        lcl_val = max(0.0, target - (control_limit_l * sigma * factor))
        lcl_series.append(round(lcl_val, 2))

    ewma_latest = ewma_series[-1]
    lcl_latest = lcl_series[-1]
    avg_patients = round(mean_x, 1)
    avg_consult = round(sum(consults) / len(consults), 1) if consults else None
    punctuality_avg = round(sum(punctualities) / len(punctualities), 1) if punctualities else None

    half_n = max(1, n // 2)
    baseline_ewma = sum(ewma_series[:half_n]) / half_n

    if avg_consult is None:
        consult_status, consult_is_ok = 'Tiada Rekod Masa', True
    elif avg_consult > 15.0:
        consult_status, consult_is_ok = 'Melebihi 15m (Risiko Keletihan)', False
    elif avg_consult < 8.0:
        consult_status, consult_is_ok = 'Terlalu Singkat (<8m)', False
    else:
        consult_status, consult_is_ok = 'Julat Optimum (10–15m)', True

    is_alert = (
        ewma_latest < lcl_latest
        or ewma_latest < 26.0
        or (avg_consult is not None and avg_consult > 18.0)
    )
    is_watch = (
        ewma_latest < 32.5
        or (n >= 4 and baseline_ewma > 0 and ewma_latest < baseline_ewma * 0.85)
        or (avg_consult is not None and (avg_consult > 15.0 or avg_consult < 8.0))
    )

    if is_alert:
        status = 'ALERT'
        badge_label = 'Alert • Burnout Risk'
        badge_bg, badge_color, badge_border = '#fee2e2', '#dc2626', '#fecaca'
        card_bg, card_border = '#fffafa', '#fecaca'
        coaching_msg = (
            f'EWMA semasa ({ewma_latest:.1f} kes/hari) melepasi had kawalan bawah '
            'atau purata masa konsultasi tinggi. Sila ambil rehat taktikal dan bincang agihan tugas.'
        )
        surveillance_note = (
            f'Amaran Burnout/Drift: EWMA ({ewma_latest:.1f} kes/hari) berada di bawah '
            f'LCL ({lcl_latest:.1f}) atau masa konsultasi melebihi 18 minit.'
        )
    elif is_watch:
        status = 'WATCH'
        badge_label = 'Watch • Early Drift'
        badge_bg, badge_color, badge_border = '#fef3c7', '#d97706', '#fde68a'
        card_bg, card_border = '#fffdf5', '#fde68a'
        coaching_msg = (
            f'Rentak EWMA ({ewma_latest:.1f} kes/hari) atau tempoh konsultasi menunjukkan tanda keletihan awal. '
            'Jaga rentak klinikal dan berehat seketika jika perlu.'
        )
        surveillance_note = (
            f'Pemerhatian Awal (Watch): EWMA ({ewma_latest:.1f} kes/hari) atau purata konsultasi '
            f'({avg_consult if avg_consult is not None else "—"} min) mula menyusut.'
        )
    else:
        status = 'IN_CONTROL'
        badge_label = 'In Control • Optimal'
        badge_bg, badge_color, badge_border = '#dcfce7', '#16a34a', '#bbf7d0'
        card_bg, card_border = '#f0fdf4', '#bbf7d0'
        coaching_msg = (
            f'Prestasi EWMA ({ewma_latest:.1f} kes/hari) stabil dan seimbang. '
            'Teruskan momentum kerja yang sihat!'
        )
        surveillance_note = (
            f'Trajektori EWMA ({ewma_latest:.1f} kes/hari) stabil di atas LCL ({lcl_latest:.1f}) '
            f'dengan masa konsultasi purata {avg_consult if avg_consult is not None else "—"} min/pesakit.'
        )

    return {
        'has_data': True,
        'status': status,
        'badge_label': badge_label,
        'badge_bg': badge_bg,
        'badge_color': badge_color,
        'badge_border': badge_border,
        'card_bg': card_bg,
        'card_border': card_border,
        'dates': dates,
        'raw_series': xs,
        'ewma_series': ewma_series,
        'lcl_series': lcl_series,
        'ewma_latest': ewma_latest,
        'lcl_latest': lcl_latest,
        'avg_patients': avg_patients,
        'avg_consult': avg_consult,
        'punctuality_avg': punctuality_avg,
        'consult_status': consult_status,
        'consult_is_ok': consult_is_ok,
        'coaching_msg': coaching_msg,
        'surveillance_note': surveillance_note,
        'observation_count': n,
        'target': target,
        'lambda': lam,
    }


def get_productivity_ewma_profile(user, as_of=None, window_days=30):
    """Tarik log ProductivityLog pegawai dalam tetingkap hari dan kira profil EWMA."""
    if as_of is None:
        as_of = timezone.localdate()
    elif isinstance(as_of, datetime):
        if timezone.is_naive(as_of):
            as_of = timezone.make_aware(as_of)
        as_of = timezone.localtime(as_of).date()

    if user is None:
        profile = _compute_productivity_ewma([])
        profile['has_any_records'] = False
        return profile

    all_productivity = ProductivityLog.objects.filter(officer=user)
    has_any_records = all_productivity.exists()
    start_date = as_of - timedelta(days=max(1, window_days) - 1)
    rows = all_productivity.filter(
        record_date__gte=start_date,
        record_date__lte=as_of,
    ).order_by('record_date', 'id').values_list(
        'record_date',
        'patients_cleared',
        'avg_consult_minutes',
        'punctuality_rate',
    )

    by_date = {}
    for rec_date, cleared, consult_mins, punc_rate in rows:
        by_date[rec_date] = (
            rec_date,
            max(0, cleared or 0),
            consult_mins,
            punc_rate,
        )

    ordered_entries = [by_date[d] for d in sorted(by_date.keys())]
    profile = _compute_productivity_ewma(ordered_entries)
    profile['has_any_records'] = has_any_records
    return profile
