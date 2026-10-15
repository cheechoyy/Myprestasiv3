from .models import (
    ProcedureLog, PolicyDeclaration, DocumentationLog,
    CPDClaim, ProductivityLog, InnovationProject,
    MyPSQFeedback, EmotionalReflection, PeerKudos, MentorshipSession
)

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
    cpd_claims = CPDClaim.objects.filter(officer=user)
    score_knowledge = min(100.0, (sum([c.points for c in cpd_claims]) / 50.0) * 100.0) if cpd_claims.exists() else 90.0

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
    score_clinical = int((score_delivery + score_knowledge + score_policy + score_doc) / 4.0)


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
    emotions = EmotionalReflection.objects.filter(officer=user)
    score_stress = (sum([e.self_awareness_score for e in emotions]) / emotions.count()) if emotions.exists() else 85.0

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
        'score_cpd': int(score_knowledge),  # <--- TAMBAHKAN KUNCI INI
        'pri_score': pri_score,
        'promotion_tier': tier,
        'promotion_color': color,
        'next_grade': next_grade,
    }