"""
checklist_items.py

The fixed, literal RFP go/no-go checklist SPS uses, ported directly from the
company's Financial/Legal/Operations/Technical checklist PDF. This list is the
ground truth: the AI engine is required to answer every one of these items for
every RFP, rather than inventing its own set of things to check.

Two items carry a hard, deterministic decision rule (Payment Terms and
Insurance Requirements) that the app enforces in code rather than trusting the
model's judgment on, since these are pass/fail thresholds already fixed by SPS.
"""

CHECKLIST_ITEMS = [
    # FINANCIAL / ACCOUNTING
    {"category": "financial", "item": "Payment Terms",
     "question": "Is the payment plan NET30 or better?"},
    {"category": "financial", "item": "Financial Stability Requirements",
     "question": "Does the RFP require financial statements or other proof of financial stability?"},
    {"category": "financial", "item": "Unaudited Financial Statements",
     "question": "Are unaudited financial statements acceptable, or does the RFP require audited statements?"},
    {"category": "financial", "item": "Insurance Requirements",
     "question": "What insurance coverage is required, and does it total $5,000,000 or less?"},
    {"category": "financial", "item": "Profitability Analysis",
     "question": "Based on the pricing structure, can expected revenue reasonably cover projected costs?"},
    {"category": "financial", "item": "Bid Bond",
     "question": "Is a bid bond or proposal bond required at submission?"},

    # LEGAL
    {"category": "legal", "item": "Relevant Experience",
     "question": "Does the RFP require a minimum amount of relevant prior experience?"},
    {"category": "legal", "item": "Registration Requirement",
     "question": "Is company registration (state, procurement portal, etc.) required?"},
    {"category": "legal", "item": "Financial Statement of Previous Year",
     "question": "Is the prior year's financial statement required?"},
    {"category": "legal", "item": "Qualified Personnel",
     "question": "Does the RFP specify qualified-personnel or key-staff requirements?"},
    {"category": "legal", "item": "Technical Knowhow",
     "question": "Does the RFP require specific technical expertise or know-how?"},
    {"category": "legal", "item": "Expected Revenue Generation",
     "question": "Is contract value or expected revenue estimable from the pricing terms?"},
    {"category": "legal", "item": "Period of Implementation",
     "question": "Is the implementation period or contract duration clearly defined?"},
    {"category": "legal", "item": "Insurance Coverage",
     "question": "Are all required insurance coverages clearly and fully stated?"},
    {"category": "legal", "item": "Compliance of Law",
     "question": "Does the RFP require compliance with applicable laws and regulations generally?"},
    {"category": "legal", "item": "Compliance Requirements (Data Protection)",
     "question": "Are data protection laws or standards referenced (e.g. breach notification statutes, CJIS, HIPAA, FERPA)?"},
    {"category": "legal", "item": "State Registration",
     "question": "Is registration in the state where the project will be executed required?"},
    {"category": "legal", "item": "E-Verify",
     "question": "Does the RFP require participation in the E-Verify employment eligibility system?"},
    {"category": "legal", "item": "Contractual Obligations",
     "question": "Are termination clauses, liability limits, and dispute resolution terms defined?"},

    # OPERATIONS
    {"category": "operations", "item": "Insurance Requirement Form",
     "question": "Is a certificate of insurance or similar form required with the proposal?"},
    {"category": "operations", "item": "Information Form (Tax ID, Owner Name, % Ownership)",
     "question": "Is a vendor/company information form (Tax ID, owner name, ownership %) required?"},
    {"category": "operations", "item": "Small Business Certification",
     "question": "Is small-business certification required or evaluated?"},
    {"category": "operations", "item": "MBE Certification",
     "question": "Is MBE / minority-owned business certification required or evaluated?"},
    {"category": "operations", "item": "Workers Comp Insurance",
     "question": "Is Workers' Compensation insurance required?"},
    {"category": "operations", "item": "Business with Iran",
     "question": "Is a declaration regarding business dealings with sanctioned countries (e.g. Iran) required?"},
    {"category": "operations", "item": "Submission Deadlines",
     "question": "Are submission deadlines clearly stated?"},
    {"category": "operations", "item": "Document Compliance",
     "question": "Are formatting and submission requirements clearly defined?"},
    {"category": "operations", "item": "Signatory Authority",
     "question": "Is a specific authorized signatory required for the proposal?"},
    {"category": "operations", "item": "Vendor Registration",
     "question": "Is registration in the client's e-procurement or vendor portal required?"},

    # TECHNICAL
    {"category": "technical", "item": "Scope of Services/Products Alignment",
     "question": "Does the RFP's scope align with the company's services and capabilities?"},
    {"category": "technical", "item": "Technical Requirements",
     "question": "Do the technical specifications match the company's capabilities?"},
    {"category": "technical", "item": "Compliance with Industry Standards",
     "question": "Does the RFP require compliance with industry standards (e.g. NIST)?"},
    {"category": "technical", "item": "Security Considerations",
     "question": "Are security requirements stated (encryption, access control, data protection)?"},
    {"category": "technical", "item": "Integration Needs",
     "question": "Does the project require integration with other systems?"},
]

CATEGORY_META = {
    "financial": {"title": "Financial / Accounting", "emoji": "\U0001F4B0"},
    "legal": {"title": "Legal", "emoji": "\u2696\uFE0F"},
    "operations": {"title": "Operations", "emoji": "\U0001F5C2\uFE0F"},
    "technical": {"title": "Technical", "emoji": "\U0001F6E0\uFE0F"},
}

CATEGORY_ORDER = ["financial", "legal", "operations", "technical"]

# Default SPS company profile, editable in the app sidebar. Values reflect
# what's actually in SPS's checklist rules (NET30 payment terms acceptable,
# $5M insurance ceiling) plus reasonable defaults for the rest.
DEFAULT_COMPANY_PROFILE = {
    "company_name": "SPS",
    "services": "Identity and Access Management (IAM), cybersecurity solutions, SOC/SIEM monitoring",
    "years_experience": 8,
    "max_insurance_available_usd": 5_000_000,
    "acceptable_payment_terms_days": 30,
    "certifications": "NIST 800-53 aligned, SOC 2, E-Verify enrolled",
    "annual_revenue_usd": 12_000_000,
    "can_provide_audited_financials": True,
    "registered_states": "Home state only (register elsewhere on a per-bid basis)",
}
