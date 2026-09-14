import streamlit as st

from agents.research_agent import run_research
from config.settings import settings
from reporting.report_generator import build_report
from utils.logger import RunLogger

st.set_page_config(page_title="Public-Web Background Research Agent", layout="wide")
st.title("AI Public-Web Background Research Agent")
st.caption(
    "Enter a person's name. The system searches publicly accessible web sources, "
    "verifies claims across sources, and produces a cited report. Nothing about a "
    "specific person is hardcoded — every run is a live search."
)

missing = settings.missing_keys()
if missing:
    st.warning(
        "Missing configuration: " + ", ".join(missing) + ". "
        "Add them to your `.env` file (copy `.env.example` to `.env` first) before running research."
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
            st.markdown(report_md)

            with st.expander("All sources (quick links)"):
                seen = set()
                for e in analysis.evidence:
                    if e.source_url not in seen:
                        seen.add(e.source_url)
                        st.link_button(f"Open Source: {e.source_domain}", e.source_url)

            with st.expander("Debug log"):
                st.code(logger.text())
