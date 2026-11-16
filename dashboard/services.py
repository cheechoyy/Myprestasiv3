from datetime import date, datetime, timedelta

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

    # 5. Productivity
    all_productivity = ProductivityLog.objects.filter(officer=user)
    has_productivity_records = all_productivity.exists()
    productivity_start = as_of - timedelta(days=29)
    productivity_rows = all_productivity.filter(
        record_date__gte=productivity_start,
        record_date__lte=as_of,
    ).order_by('record_date', 'id').values_list(
        'record_date', 'patients_cleared'
    )
    patients_by_date = {}
    for record_date, patients_cleared in productivity_rows:
        patients_by_date[record_date] = max(0, patients_cleared or 0)

    if not patients_by_date:
        modules.append(_module_result(
            'productivity', 'Productivity',
            status='NO_DATA', reason='Tiada log produktiviti 30 hari terakhir.',
            has_records=has_productivity_records,
            source='ProductivityLog.patients_cleared',
            target=38.22,
        ))
    else:
        average_patients = (
            sum(patients_by_date.values()) / len(patients_by_date)
        )
        score = _clamp_score(50.0 * average_patients / 38.22)
        modules.append(_module_result(
            'productivity', 'Productivity',
            score=score,
            n=len(patients_by_date),
            denominator=30,
            raw_value=average_patients,
            status=_score_status(score),
            has_records=True,
            source='ProductivityLog.patients_cleared',
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
        total_points = sum(kudos_points)
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
