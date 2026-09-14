import datetime

import streamlit as st

from agents.amc_agent import run_amc
from agents.research_agent import run_research
from config.settings import settings
from reporting.amc_report import build_amc_report
from reporting.report_generator import build_report
from reporting.word_export import markdown_to_docx_bytes
from utils.logger import RunLogger

st.set_page_config(page_title="Open-Source Research Agent", layout="wide")
st.title("AI Open-Source Research Agent")

missing = settings.missing_keys()
if missing:
    st.warning(
        "Missing configuration: " + ", ".join(missing) + ". "
        "Add them to your `.env` file (copy `.env.example` to `.env` first) before running research."
    )

mode = st.radio(
    "Mode",
    ["Adverse Media Check (company + directors)", "Person Background Research"],
    horizontal=True,
)

EXAMPLE_BRIEF = """Conduct a structured, open source-based Adverse Media Check (AMC) for \
EXAMPLE PRIVATE LIMITED CIN: U00000XX0000XXX000000 PAN: AAAAA0000A and all its directors, \
using only publicly available credible information, watchoutinvestors.com, RBI defaulter list \
and other credible sources. The objective is to identify any adverse media, regulatory actions, \
economic offences, criminal allegations, terrorism related mentions, or major reputational risks \
reported in reliable sources. Provide a concise summary covering:
- Whether any adverse media was found for the company
- Whether any adverse media was found for each director
- Type of issues (e.g., criminal case, economic offence, ED/CBI/SFIO action, tax raid, regulatory penalty)
- Match classification (Strong / Weak / Uncertain)
- Overall risk impression (Low / Medium / High)
- Major disclaimers and limitations, include the date of search."""


def _render_downloads(report_md: str, basename: str) -> None:
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in basename)[:60] or "report"
    col1, col2 = st.columns(2)
    with col1:
        try:
            st.download_button(
                "Download as Word (.docx)",
                data=markdown_to_docx_bytes(report_md, title=basename),
                file_name=f"{safe}-{stamp}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                type="primary",
            )
        except Exception as e:  # export must never take the report down with it
            st.error(f"Word export failed: {e}")
    with col2:
        st.download_button(
            "Download as Markdown (.md)",
            data=report_md,
            file_name=f"{safe}-{stamp}.md",
            mime="text/markdown",
        )


# ---------------------------------------------------------------------------
# Adverse Media Check
# ---------------------------------------------------------------------------
if mode.startswith("Adverse"):
    st.caption(
        "Paste the research brief. The model reads it and decides what to search — which "
        "topics, which named sources, which identifiers — nothing is hardcoded. JSON briefs "
        "are accepted too."
    )

    with st.form("amc_form"):
        brief = st.text_area(
            "Research brief",
            height=260,
            placeholder=EXAMPLE_BRIEF,
            help="Free text or JSON. Include identifiers (CIN/PAN) where you have them — an "
            "identifier match is the strongest way to confirm the right entity.",
        )
        amc_submitted = st.form_submit_button("Run Adverse Media Check", type="primary")

    if amc_submitted:
        if not brief.strip():
            st.error("Please paste a research brief.")
        elif missing:
            st.error("Cannot run — missing API keys. See warning above.")
        else:
            logger = RunLogger()
            progress = st.status("Running adverse media check...", expanded=True)
            with progress:
                st.write("✓ Brief received")
                plan, company_result, director_analyses, error = run_amc(brief.strip(), logger)
                for line in logger.lines:
                    st.write(line)

            if error:
                progress.update(label="Check failed", state="error")
                st.error(error)
            else:
                progress.update(label="Adverse media check complete", state="complete")
                company, company_sources = company_result
                report_md = build_amc_report(
                    brief.strip(), plan, company, company_sources, director_analyses
                )

                st.markdown("---")
                total = len(company.adverse_findings) + sum(
                    len(a.adverse_findings) for a, _ in director_analyses.values()
                )
                c1, c2, c3 = st.columns(3)
                c1.metric("Overall risk", company.overall_risk)
                c2.metric("Adverse findings", total)
                c3.metric("Directors checked", len(director_analyses))

                _render_downloads(report_md, plan.subject_name)
                st.markdown("---")
                st.markdown(report_md)

                with st.expander("Debug log"):
                    st.code(logger.text())

# ---------------------------------------------------------------------------
# Person Background Research
# ---------------------------------------------------------------------------
else:
    st.caption(
        "Enter a person's name. The system searches publicly accessible sources, verifies "
        "claims across them, and produces a cited report. Nothing person-specific is hardcoded."
    )

    with st.form("research_form"):
        col1, col2 = st.columns(2)
        with col1:
            name = st.text_input("Person Name *", placeholder="e.g. Vedansh Kumar")
            company = st.text_input("Company / Organization (optional)")
            location = st.text_input("Location (optional)")
        with col2:
            research_type = st.selectbox(
                "Research Type",
                [
                    "Comprehensive",
                    "Professional",
                    "Education",
                    "Publications",
                    "Legal / Public Court Information",
                    "Social / Professional Profiles",
                    "Company Relationship",
                    "Custom",
                ],
            )
            custom_request = st.text_area(
                "Custom Research Request",
                placeholder="e.g. Only investigate education and professional history.",
                help="Only used when Research Type (above) is set to 'Custom'. "
                "For any other Research Type, whatever you type here is ignored.",
            )
        submitted = st.form_submit_button("Start Research", type="primary")

    if submitted:
        if not name.strip():
            st.error("Person Name is required.")
        elif missing:
            st.error("Cannot run research — missing API keys. See warning above.")
        else:
            if custom_request.strip() and research_type != "Custom":
                st.info(
                    f"Note: you wrote a Custom Research Request but Research Type is "
                    f"set to '{research_type}', so that text is being ignored this run "
                    f"— set Research Type to 'Custom' to actually use it."
                )
            if not company.strip() and not location.strip():
                st.info(
                    "Note: no Company or Location given — for a common name, this makes "
                    "it much harder to tell people with the same name apart, so the "
                    "report may come back thin or ambiguous. Adding either one usually "
                    "helps a lot."
                )
            logger = RunLogger()
            progress = st.status("Running research...", expanded=True)

            with progress:
                st.write("✓ Input validated")
                analysis, search_results, error = run_research(
                    name.strip(), company.strip(), location.strip(), research_type, custom_request.strip(), logger
                )
                for line in logger.lines:
                    st.write(line)

            if error:
                progress.update(label="Research failed", state="error")
                st.error(error)
            else:
                progress.update(label="Research complete", state="complete")
                report_md = build_report(
                    name.strip(),
                    company.strip(),
                    location.strip(),
                    research_type,
                    custom_request.strip(),
                    analysis,
                    search_results,
                    logger,
                )
                st.markdown("---")
                _render_downloads(report_md, name.strip())
                st.markdown("---")
                st.markdown(report_md)

                with st.expander("All sources (quick links)"):
                    seen = set()
                    for e in analysis.evidence:
                        if e.source_url not in seen:
                            seen.add(e.source_url)
                            st.link_button(f"Open Source: {e.source_domain}", e.source_url)

                with st.expander("Debug log"):
                    st.code(logger.text())
