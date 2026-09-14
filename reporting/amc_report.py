"""
Builds the Adverse Media Check report.

Everything factual is assembled here in Python from the structured analysis, so
a finding cannot appear in the report unless it survived evidence extraction with
a real source attached.
"""
import datetime

from models.amc_schemas import AMCAnalysis, ResearchPlan

RISK_NOTE = {
    "LOW": "No confirmed adverse media identified in the sources searched.",
    "MEDIUM": "Some adverse or unresolved items identified; review recommended.",
    "HIGH": "Significant adverse findings identified; escalation recommended.",
}


def _findings_table(analysis: AMCAnalysis) -> str:
    if not analysis.adverse_findings:
        return ""
    rows = [
        "| About | Issue type | What the source reports | Match | Severity | Source |",
        "|---|---|---|---|---|---|",
    ]
    for f in analysis.adverse_findings:
        summary = f.summary.replace("|", "/")[:220]
        rows.append(
            f"| {f.about.replace('|', '/')} | {f.issue_type.replace('|', '/')} | {summary} "
            f"| {f.match_classification} | {f.severity} | [{f.source_domain}]({f.source_url}) |"
        )
    return "\n".join(rows) + "\n"


def _subject_block(plan: ResearchPlan, analysis: AMCAnalysis) -> str:
    lines = [f"- **Entity:** {plan.subject_name}"]
    for ident in plan.identifiers:
        lines.append(f"- **{ident}**")
    lines.append(
        f"- **Entity confirmed in sources:** {'Yes' if analysis.subject_confirmed else 'No — see limitations'}"
    )
    if analysis.subject_summary:
        lines.append(f"- **What the sources establish:** {analysis.subject_summary}")
    return "\n".join(lines)


def build_amc_report(
    brief: str,
    plan: ResearchPlan,
    company: AMCAnalysis,
    company_sources,
    director_analyses: dict,
) -> str:
    searched_on = datetime.datetime.now().strftime("%d %B %Y, %H:%M")

    parts = [
        "# Adverse Media Check (AMC) Report",
        f"\n*Open-source search conducted on {searched_on}*\n",
        "## 1. Subject",
        _subject_block(plan, company),
    ]

    # --- scope ---------------------------------------------------------------
    scope = [f"- **Topics investigated:** {', '.join(plan.investigation_topics)}"]
    if plan.preferred_sources:
        scope.append(f"- **Sources specifically requested:** {', '.join(plan.preferred_sources)}")
    scope.append(f"- **Directors checked:** {'Yes' if plan.include_directors else 'No'}")
    scope.append(f"- **Search queries run:** {len(plan.queries)}")
    parts += ["\n## 2. Scope of Search", "\n".join(scope)]

    # --- company findings ----------------------------------------------------
    company_table = _findings_table(company)
    parts.append("\n## 3. Adverse Media — Company")
    if company_table:
        parts.append(f"**{len(company.adverse_findings)} adverse item(s) identified.**\n")
        parts.append(company_table)
    else:
        parts.append(
            f"**No adverse media was identified for {plan.subject_name} in the sources searched.**\n\n"
            f"{company.clean_assessment}"
        )

    # --- directors -----------------------------------------------------------
    parts.append("\n## 4. Adverse Media — Directors")
    if not plan.include_directors:
        parts.append("_Director checks were not requested in the brief._")
    elif not company.directors:
        parts.append(
            "_No directors could be identified from the publicly available sources searched. "
            "Director details may require a paid registry lookup (e.g. MCA)._"
        )
    else:
        named = ", ".join(d.name for d in company.directors)
        parts.append(f"**Directors identified from public sources:** {named}\n")
        for director in company.directors:
            result = director_analyses.get(director.name.strip())
            role = f" — {director.role}" if director.role else ""
            parts.append(f"\n### {director.name}{role}")
            if result is None:
                parts.append("_Not separately searched (outside the configured director limit)._")
                continue
            d_analysis, _ = result
            d_table = _findings_table(d_analysis)
            if d_table:
                parts.append(f"**{len(d_analysis.adverse_findings)} adverse item(s) identified.**\n")
                parts.append(d_table)
            else:
                parts.append(
                    f"No adverse media identified in the sources searched. {d_analysis.clean_assessment}"
                )

    # --- risk ----------------------------------------------------------------
    all_findings = list(company.adverse_findings)
    for d_analysis, _ in director_analyses.values():
        all_findings.extend(d_analysis.adverse_findings)
    strong = sum(1 for f in all_findings if f.match_classification == "STRONG")

    parts += [
        "\n## 5. Overall Risk Impression",
        f"- **Risk rating:** **{company.overall_risk}**",
        f"- **Basis:** {company.risk_reasoning}",
        f"- **Findings:** {len(all_findings)} total across company and directors "
        f"({strong} classified STRONG match)",
        f"- {RISK_NOTE.get(company.overall_risk, '')}",
    ]

    # --- sources -------------------------------------------------------------
    parts.append("\n## 6. Sources")
    cited = {}
    for f in all_findings:
        if f.source_url:
            cited[f.source_url] = f.source_domain
    if cited:
        rows = ["| # | Domain | Link |", "|---|---|---|"]
        for i, (url, domain) in enumerate(cited.items(), start=1):
            rows.append(f"| {i} | {domain} | [{url}]({url}) |")
        parts.append("**Sources supporting the findings above:**\n")
        parts.append("\n".join(rows) + "\n")
    else:
        parts.append("_No source produced a confirmed adverse finding._\n")

    reviewed = [r for r in company_sources if r.url not in cited]
    if reviewed:
        rows = ["| # | Domain | Link |", "|---|---|---|"]
        for i, r in enumerate(reviewed, start=1):
            rows.append(f"| {i} | {r.domain} | [{r.url}]({r.url}) |")
        parts.append(
            f"\n**Also searched and reviewed — {len(reviewed)} page(s) that produced no adverse "
            f"finding** (listed for traceability):\n"
        )
        parts.append("\n".join(rows) + "\n")

    # --- limitations ---------------------------------------------------------
    limitations = list(company.limitations)
    limitations += [
        "This check covers only publicly accessible open sources. Paid registries, "
        "subscription databases and login-protected sites were not accessed.",
        "Absence of adverse media is not evidence of absence of adverse events — it means "
        "nothing was found in the sources searched on the date above.",
        "Entity and individual matching is based on publicly available identifiers and "
        "contextual signals. Items classified WEAK or UNCERTAIN may relate to a different "
        "entity or person with a similar name.",
        "Findings reported as allegations, investigations or charges are not determinations "
        "of guilt or liability.",
    ]
    parts.append("\n## 7. Disclaimers and Limitations")
    parts.append("\n".join(f"- {item}" for item in limitations))
    parts.append(f"\n*Search date: {searched_on}*")

    return "\n\n".join(parts) + "\n"
