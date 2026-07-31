"""
knowledge_base.py

Dummy/sample structured company knowledge base for SPS — the foundation for
AI Response Generation (Phase 6) and, later, full Proposal Assembly (Phase 7).

ALL VALUES HERE ARE SAMPLE DATA for development/demo purposes, matching
Section 3 of the team's Combined Project Plan (3.1 Company Profile through
3.10 Proposal Templates). Replace with SPS's real information before any
real client-facing use.

DELIBERATELY NO VECTOR DATABASE / RETRIEVAL LAYER: the whole knowledge base
below is small enough to pass directly into a Gemini prompt as grounding
context. This gives the same "answer only from real company data" benefit
a full RAG pipeline would, without needing to stand up vector-DB
infrastructure in a short timeframe — a reasonable simplification for a
company of this size and a single knowledge base that easily fits in a
single prompt's context window.
"""

COMPANY_PROFILE = {
    "name": "SPS",
    "overview": (
        "SPS is a cybersecurity and identity management firm specializing in Identity and "
        "Access Management (IAM), Security Operations Center (SOC) monitoring, and secure "
        "application development for public-sector and enterprise clients."
    ),
    "mission": "To make secure, compliant technology accessible and reliable for every client we serve.",
    "vision": "To be the trusted security and identity partner of choice for public-sector organizations.",
    "years_experience": 8,
    "company_size": "45 employees",
    "office_locations": ["Rawalpindi, Pakistan (HQ)", "Remote — US client support"],
    "contact": {"email": "contact@sps-example.com", "phone": "+92-51-000-0000"},
}

SERVICES = [
    "Identity and Access Management (IAM)",
    "Cybersecurity Solutions",
    "SOC / SIEM Monitoring",
    "AI/ML-driven solutions (search, personalization, data platforms)",
    "Web Application Development and Integration",
    "Cloud Solutions and DevOps",
]

TECH_STACK = {
    "frontend": ["React", "Streamlit"],
    "backend": ["Python", "FastAPI", "Node.js"],
    "database": ["PostgreSQL", "MongoDB"],
    "cloud_platforms": ["AWS", "Microsoft Azure"],
    "devops": ["Docker", "GitHub Actions CI/CD"],
    "ai_technologies": ["Google Gemini", "OpenAI GPT", "LangChain"],
    "security_tools": ["Okta", "Splunk SIEM", "CrowdStrike"],
}

TEAM = {
    "developers": 20,
    "software_architects": 3,
    "project_managers": 4,
    "business_analysts": 3,
    "qa_engineers": 5,
    "devops_engineers": 3,
    "certifications_held": ["CISSP", "AWS Certified Solutions Architect", "PMP", "CEH"],
}

PROJECT_PORTFOLIO = [
    {
        "client_industry": "Higher Education",
        "description": "Implemented Single Sign-On (SSO) and multi-factor authentication across 12 university departments.",
        "technologies": ["Okta", "SAML", "Python", "React"],
        "team_size": 6,
        "duration_months": 8,
        "challenges": "Legacy system integration across departments with inconsistent authentication standards.",
        "achievements": "Reduced help-desk password-reset tickets by 60% within the first quarter post-launch.",
    },
    {
        "client_industry": "State Government",
        "description": "24/7 SOC/SIEM monitoring for a state health-exchange platform.",
        "technologies": ["Splunk", "AWS", "Python"],
        "team_size": 5,
        "duration_months": 12,
        "challenges": "Meeting strict compliance and audit-logging requirements under a tight go-live deadline.",
        "achievements": "Zero missed critical alerts over 12 months of monitoring; passed external security audit with no findings.",
    },
    {
        "client_industry": "Healthcare",
        "description": "HIPAA-compliant identity management platform for a multi-site hospital network.",
        "technologies": ["Okta", "Azure AD", "Python"],
        "team_size": 4,
        "duration_months": 6,
        "challenges": "Coordinating phased rollout across multiple hospital sites without service downtime.",
        "achievements": "100% HIPAA audit pass rate in the first post-implementation review.",
    },
]

SECURITY_COMPLIANCE = {
    "certifications": ["NIST 800-53 aligned", "SOC 2 Type II", "ISO 27001 (in progress)"],
    "standards_supported": ["GDPR", "HIPAA", "OWASP Top 10"],
    "data_encryption": "AES-256 at rest, TLS 1.3 in transit",
    "backup_disaster_recovery": "Daily automated backups, 4-hour recovery time objective (RTO), geo-redundant storage",
    "access_control": "Role-based access control (RBAC) with mandatory multi-factor authentication for privileged accounts",
}

DEVELOPMENT_METHODOLOGY = {
    "process": "Agile / Scrum, 2-week sprint cycles",
    "qa_strategy": "Automated unit and integration testing, mandatory peer code review before every merge",
    "cicd_pipeline": "GitHub Actions — automated build, test, and staged deployment pipeline",
}

FINANCIAL_INFO = {
    "pricing_models": ["Hourly Rate", "Fixed Cost"],
    "payment_terms": "NET 30",
    "maintenance_support_plans": ["Basic (business hours)", "Premium (24/7, with SLA)"],
}

LEGAL_INFO = {
    "nda": "Standard mutual NDA available on request prior to detailed technical discussions.",
    "sla": "99.9% uptime SLA on all managed services.",
    "intellectual_property": "Client owns all deliverable IP upon final payment.",
    "warranty": "90-day defect warranty on all delivered software post go-live.",
}

PROPOSAL_TEMPLATES = {
    "cover_letter": "Standard SPS cover letter template (sample).",
    "executive_summary": "Standard SPS executive summary template (sample).",
    "company_profile_document": "SPS_Company_Profile.pdf (sample).",
    "case_studies": "Available on request, matched to the relevant industry/vertical.",
}


def get_full_knowledge_base() -> dict:
    """
    Returns the entire knowledge base as one dict. Small enough to pass
    directly into a Gemini prompt as grounding context for AI Response
    Generation (Phase 6) — no retrieval/vector search step needed at this
    scale; the whole thing fits comfortably within a single prompt.
    """
    return {
        "company_profile": COMPANY_PROFILE,
        "services": SERVICES,
        "tech_stack": TECH_STACK,
        "team": TEAM,
        "project_portfolio": PROJECT_PORTFOLIO,
        "security_compliance": SECURITY_COMPLIANCE,
        "development_methodology": DEVELOPMENT_METHODOLOGY,
        "financial_info": FINANCIAL_INFO,
        "legal_info": LEGAL_INFO,
        "proposal_templates": PROPOSAL_TEMPLATES,
    }
