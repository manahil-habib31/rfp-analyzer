# 📄 RFP Analyzer

AI-powered Go/No-Go decision support tool for Request for Proposal (RFP) documents.

Upload an RFP, compare it against a configurable company profile, and get an instant fit
score, verdict, and department-by-department compliance breakdown — instead of manually
reading 20+ pages to figure out whether you should even bid.

## 🚀 What It Does

Most RFPs bury the things that actually matter — insurance minimums, bond requirements,
mandatory deliverables, evaluation weights — inside dense procurement language. RFP
Analyzer reads the document, compares it against a configurable company profile and a
fixed 34-item SPS checklist (Financial, Legal, Operations, Technical), and surfaces:

- ✅ **Fit Score (0–100)** and a clear verdict — **GO / CONDITIONAL / NO-GO**
- 📦 **Deliverables** — mandatory vs. optional, with effort estimates
- 📊 **Evaluation Criteria** — weighted scoring breakdown as defined in the RFP
- 🧾 **Compliance Checklist** — the exact 34-item SPS checklist, split by department
  (Financial, Legal, Operations, Technical), each item flagged **MET / GAP / REVIEW**
- 📅 **Key Dates & Budget** — deadlines, contract value, payment terms, insurance, bonding
- ⚠️ **Risk Assessment** — top risks and a go/no-go recommendation with reasoning

Export the result as a clean Markdown report or a styled PDF case file.

## 🛠️ Tech Stack

| Layer | Tool |
|---|---|
| UI | Streamlit |
| PDF text extraction | pdfplumber |
| AI analysis | Google Gemini (`gemini-2.5-flash`, structured JSON output) |
| PDF report generation | ReportLab |
| Config | python-dotenv |

> **Note on the Gemini SDK:** this project uses the current **`google-genai`** package
> rather than the older `google-generativeai` package. Google [deprecated
> `google-generativeai`](https://github.com/google-gemini/deprecated-generative-ai-python)
> — it no longer receives updates — so `google-genai` is the correct choice for anything
> built now.

## 📂 Project Structure

```
rfp_analyzer/
├── app.py               # Streamlit UI — upload, company profile, results, downloads
├── ai_engine.py          # Gemini prompt, structured JSON parsing, retry/backoff logic
├── pdf_reader.py          # PDF text extraction (pdfplumber)
├── pdf_report.py          # Generates the styled downloadable PDF report
├── checklist_items.py      # The fixed 34-item SPS checklist + default company profile
├── sample_rfp.pdf           # Synthetic test RFP (public-sector IAM/cybersecurity procurement)
├── requirements.txt
└── .env.example             # Copy to .env and add your GEMINI_API_KEY
```

## ⚙️ How It Works

1. Upload an RFP PDF through the Streamlit interface (or click "Load sample RFP").
2. Extract text from every page (`pdf_reader.py`).
3. Edit the **company profile** in the sidebar — services, years of experience, max
   insurance available, acceptable payment terms, certifications, revenue.
4. Click **Analyze** — the RFP text, company profile, and the fixed 34-item checklist are
   sent to Gemini with a strict JSON schema, so the model returns structured data (not
   free-text) covering deliverables, evaluation criteria, per-item compliance status,
   dates/budget, and an overall fit assessment.
5. Review results across sectioned tabs, with a verdict card and key metrics (fit score,
   deliverables/estimated weeks, items met/gaps) up top.
6. Export the full analysis as Markdown or PDF.

## 🧠 Key Design Decisions

- **Fixed 34-item checklist, not free-text generation.** The AI doesn't get to invent or
  skip compliance items — it must answer every one of SPS's actual checklist items
  (`checklist_items.py`), and the app merges the model's response back onto that fixed
  list by name so the report always covers exactly the checklist, regardless of how the
  model orders its output.
- **Two hard-coded decision rules, not left to model judgment.** Payment Terms (NET30 or
  better → MET) and Insurance Requirements (≤ company's max coverage → MET) are two
  pass/fail thresholds SPS already has fixed policy on, so the prompt requires the model
  to apply them literally rather than use its own judgment — keeping those two calls
  consistent across every run.
- **Structured JSON over free-text parsing** — asking the model for markdown and slicing
  it by header position is fragile. Forcing strict JSON output (`response_mime_type:
  application/json`) makes scoring, counting, and status badges computable rather than
  guessed.
- **Retry with backoff, but quota-aware** — transient errors (429 rate limits, 500/503
  server errors) are retried automatically with exponential backoff. Daily quota
  exhaustion is detected separately (by inspecting the error message for "quota" + "day")
  and fails fast with a clear message, since retrying a daily cap is pointless.
- **Session-state-based flow** — Streamlit reruns the whole script on every interaction,
  so the upload/analyze flow uses `st.session_state` to avoid re-triggering the (rate
  limited) Gemini call on unrelated UI interactions like switching tabs.

## ▶️ Running Locally

```bash
cd rfp_analyzer
pip install -r requirements.txt

# Add your Gemini API key (free at https://aistudio.google.com/app/apikey)
cp .env.example .env
# then edit .env and paste your key

streamlit run app.py
```

The app also accepts the API key directly in the sidebar if you'd rather not use a
`.env` file (handy for quick testing).

## 📌 Notes

- Built and tested against a synthetic sample RFP (a fictional county government IAM /
  cybersecurity procurement) designed to exercise every analysis path — mandatory/
  optional deliverables, weighted criteria, NET30 payment terms, and a $3M insurance
  requirement (within SPS's $5M ceiling).
- Gemini's free tier is rate-limited (roughly 10 requests/minute, 500/day on
  `gemini-2.5-flash` as of mid-2026) — for heavier testing, enable billing on the
  underlying Google Cloud project.
- Tested end-to-end during development: server startup, sidebar rendering, sample RFP
  loading, and PDF report generation were all verified with an automated headless
  browser pass before delivery.
