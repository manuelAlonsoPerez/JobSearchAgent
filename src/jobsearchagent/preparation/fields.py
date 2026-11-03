"""Structured CV fields for the two Canva layouts."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .source import SourceCv, TemplateId

_SPLIT_LIST = re.compile(r"[\n;]|,(?=\s)")

LINKEDIN_URL = "https://www.linkedin.com/in/manuel-alonso-a75807154/"


class CvModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ContactFields(CvModel):
    phone: str = ""
    email: str = ""
    street: str = ""
    city: str = ""
    linkedin_label: str = "Linkedin"
    linkedin_url: str = LINKEDIN_URL


class ExperienceRole(CvModel):
    organization: str
    title: str
    period: str
    bullets: list[str] = Field(default_factory=list)

    @field_validator("bullets", mode="before")
    @classmethod
    def _coerce_bullets(cls, value: Any) -> list[str]:
        return _coerce_str_list(value)


class EducationItem(CvModel):
    years: str
    school: str
    details: list[str] = Field(default_factory=list)

    @field_validator("details", mode="before")
    @classmethod
    def _coerce_details(cls, value: Any) -> list[str]:
        return _coerce_str_list(value)


class CvFields(CvModel):
    name: str
    headline: str
    summary: str
    skills: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    experience: list[ExperienceRole] = Field(default_factory=list)
    education: list[EducationItem] = Field(default_factory=list)
    contact: ContactFields = Field(default_factory=ContactFields)

    @field_validator("skills", "languages", mode="before")
    @classmethod
    def _coerce_lists(cls, value: Any) -> list[str]:
        return _coerce_str_list(value)


def _coerce_str_list(value: Any) -> list[str]:
    """Accept a string or list so sloppy writer JSON still parses."""

    if value is None:
        return []
    if isinstance(value, str):
        parts = [part.strip() for part in _SPLIT_LIST.split(value) if part.strip()]
        return parts or [value.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


_SHARED_LANGUAGES = [
    "English (Fluent)",
    "Norwegian (Fluent)",
    "Spanish (Mother tongue)",
]

_SHARED_EDUCATION = [
    EducationItem(
        years="2014-2017",
        school="UNIVERSITET I OSLO",
        details=["Bachelor in mathematics, and informatics"],
    ),
    EducationItem(
        years="2000 - 2005",
        school="UNIVERSIDAD AUTONOMA DE MADRID",
        details=["MSc in Mathematics", "162/300 ECTS completed"],
    ),
]

_SHARED_CONTACT = ContactFields(
    phone="+47 451 00 934",
    street="Gunnar Schjelderupsvei",
    city="Oslo, Norway",
    linkedin_label="Linkedin",
    linkedin_url=LINKEDIN_URL,
)


def baseline_fields(template_id: TemplateId) -> CvFields:
    """Unmodified structured copy of the matching source PDF."""

    if template_id == "servicenow":
        return _SERVICENOW
    if template_id == "ai":
        return _AI
    return _FULLSTACK


def initial_fields(source: SourceCv) -> CvFields:
    """Baseline fields with profile name, headline, and email overlaid."""

    fields = baseline_fields(source.template_id).model_copy(deep=True)
    profile = source.context.get("profile") or {}
    shared = source.context.get("shared") or {}
    candidate = shared.get("candidate") if isinstance(shared, dict) else {}
    if isinstance(candidate, dict):
        if candidate.get("name"):
            fields.name = str(candidate["name"])
        if candidate.get("phone"):
            fields.contact.phone = str(candidate["phone"])
        based_in = str(candidate.get("based_in") or "").strip()
        if based_in:
            fields.contact.city = based_in
    if isinstance(profile, dict):
        if profile.get("title"):
            fields.headline = str(profile["title"])
        if profile.get("email"):
            fields.contact.email = str(profile["email"])
    return fields


def fields_to_grounding_text(fields: CvFields) -> str:
    """Flatten fields for fact-checking against the rewrite."""

    lines = [
        fields.name,
        fields.headline,
        fields.summary,
        fields.contact.phone,
        fields.contact.email,
        fields.contact.street,
        fields.contact.city,
        *fields.skills,
        *fields.languages,
    ]
    for job in fields.experience:
        lines.extend([job.organization, job.title, job.period, *job.bullets])
    for item in fields.education:
        lines.extend([item.years, item.school, *item.details])
    return "\n".join(line for line in lines if line)


def profile_allowlist(context: dict[str, Any]) -> dict[str, set[str]]:
    """Employers, titles, periods, and skill tokens claimed on the profile."""

    profile = context.get("profile") or {}
    shared = context.get("shared") or {}
    orgs: set[str] = set()
    titles: set[str] = set()
    periods: set[str] = set()
    skills: set[str] = set()
    for job in profile.get("experience") or []:
        if not isinstance(job, dict):
            continue
        if job.get("organization"):
            orgs.add(str(job["organization"]))
        if job.get("client"):
            orgs.add(str(job["client"]))
        if job.get("title"):
            titles.add(str(job["title"]))
        if job.get("period"):
            periods.add(str(job["period"]))
    for skill in profile.get("skills") or []:
        if isinstance(skill, dict) and skill.get("name"):
            skills.add(str(skill["name"]))
            for alias in skill.get("aliases") or []:
                skills.add(str(alias))
        elif skill:
            skills.add(str(skill))
    for item in profile.get("certifications") or []:
        skills.add(str(item))
    education = shared.get("education") if isinstance(shared, dict) else []
    if isinstance(education, list):
        for row in education:
            if not isinstance(row, dict):
                continue
            if row.get("school"):
                orgs.add(str(row["school"]))
            if row.get("years"):
                periods.add(str(row["years"]))
    return {
        "organizations": orgs,
        "titles": titles,
        "periods": periods,
        "skills": skills,
    }


_SERVICENOW = CvFields(
    name="Manuel Alonso",
    headline="ServiceNow Developer",
    summary=(
        "ServiceNow Developer and Consultant with 3 years of hands-on experience "
        "designing, developing, and maintaining tailored solutions on the ServiceNow "
        "platform, supported by 8+ years of broader software development experience. "
        "Skilled in ITSM, custom application development, Flow Designer, Integration Hub, "
        "REST integrations, data migration, access controls, mobile applications, testing, "
        "and technical documentation.\n\n"
        "ServiceNow Certified System Administrator (CSA) and Certified Application "
        "Developer (CAD). Experienced in translating complex business requirements into "
        "secure, maintainable solutions and supporting customers through workshops, "
        "demonstrations, training, and continuous improvement."
    ),
    skills=[
        "ServiceNow Application Development & ITSM",
        "Flow Designer and Process Automation",
        "Integration Hub and REST API Integrations",
        "JavaScript, React, Java and Python",
        "Technical Design, Testing and Documentation",
        "Agile Delivery and Cross-Functional Collaboration",
    ],
    languages=list(_SHARED_LANGUAGES),
    contact=_SHARED_CONTACT.model_copy(
        update={"email": "nexdevconsulting@outlook.com"}
    ),
    education=[item.model_copy() for item in _SHARED_EDUCATION],
    experience=[
        ExperienceRole(
            organization="Estudio Tributario Canarias",
            title="Full stack AI Developer - Freelance",
            period="2025 - PRESENT",
            bullets=[
                "Development of responsive frontend applications using React, TypeScript, and Material UI.",
                "Development of the production Pricing Wizard SaaS platform using Python, Node.js, FastAPI, React, TypeScript, and Tailwind CSS.",
                "Implementation of role-based admin and customer portals with Supabase authentication.",
                "Development of an Express.js and PostgreSQL API for pricing rules, scenarios, and integrations.",
                "Integration of AI-powered document analysis, rule extraction, and scenario summaries.",
                "Implementation of a two-service RAG platform with an Orchestrator and CrewAI-based agents.",
                "Integration of WordPress with the RAG agent system and consolidation of two Joomla websites.",
                "Development of technical documentation platforms using React, TypeScript, Astro and Docusaurus.",
                "Deployment and testing using Docker, Kubernetes, GCP, Railway, Netlify, and Playwright.",
            ],
        ),
        ExperienceRole(
            organization="Sopra Steria Norway",
            title="Senior Software Engineer",
            period="2022 - 2024",
            bullets=[
                "Analysis of existing ITSM processes and integrations to design effective replacement solutions.",
                "Design, development, and testing of tailored ServiceNow applications across ITSM, CSM, and FSM.",
                "Automation of manual processes using Flow Designer, Integration Hub, and JavaScript.",
                "Implementation of REST integrations between ServiceNow, Azure AD, and other external systems.",
                "Migration of legacy data into new ServiceNow applications and platform environments.",
                "Implementation of security controls to improve access management and data protection.",
                "Development of user interfaces, Service Portal widgets, and ServiceNow mobile applications.",
                "Automated testing, technical documentation, customer demonstrations, training, and ongoing support.",
            ],
        ),
        ExperienceRole(
            organization="Posten Norway",
            title="System developer",
            period="2019 - 2021",
            bullets=[
                "Collaborate with Digdir and other partners to build new functionalities for the interface.",
                "Shaping the team strategy and OKRs, as well as participation in workshops for product development.",
                "Development of the front-end with Javascript, React and Redux as the main technologies.",
                "Development of backend systems in Java and .NET, extending existing APIs.",
                "Writing automated tests both on the front and backend.",
                "Reduction of technical debt by re-writing outdated parts of the code.",
                "Using collaboration tools like Miro, Figma, Slack and GitHub.",
                "Experience with Azure Cosmos DB, Jupyter Notebooks, Kibana and Grafana for monitoring.",
                "Third line support.",
            ],
        ),
        ExperienceRole(
            organization="S5 Consulting",
            title="SAP and Neptune consultant",
            period="2018",
            bullets=[
                "Development of internal applications and projects on the SAP Cloud platform.",
                "Integrate between the backend systems and the SAP Cloud platform.",
                "Development of applications on Neptune's Planet 9 platform.",
                "Building applications to demonstrate the capabilities of Disruptive Technologies IoT products.",
                "Exploration of new technologies and execution of proof of concept projects.",
            ],
        ),
        ExperienceRole(
            organization="Diggit Norge",
            title="Frontend developer",
            period="2017",
            bullets=[
                "Build an e-learning platform, where I collaborated interdisciplinary with the product and design teams.",
                "Front End development with React and Redux as the main technologies used.",
                "Integrating with the .NET REST API.",
            ],
        ),
    ],
)

_FULLSTACK = CvFields(
    name="Manuel Alonso",
    headline="Full Stack Developer",
    summary=(
        "Full-stack developer with 8+ years of experience building web applications and "
        "AI-powered systems. Specializing in React on the frontend and Python on the "
        "backend, with hands-on experience designing and deploying multi-agent AI "
        "architectures, RAG pipelines, and REST API integrations in production environments.\n\n"
        "Focused on delivering end-to-end solutions — from responsive user interfaces to "
        "scalable backend services and cloud deployments. Self-directed and collaborative, "
        "with a track record of working across product, design, and engineering teams to "
        "translate complex requirements into clean, maintainable, and high-performing systems."
    ),
    skills=[
        "UI and integrations development",
        "Technical design and documentation",
        "Agile methodology and cross functional teams",
        "Detail-oriented",
    ],
    languages=list(_SHARED_LANGUAGES),
    contact=_SHARED_CONTACT.model_copy(
        update={"email": "manuelalonsoper@gmail.com"}
    ),
    education=[item.model_copy() for item in _SHARED_EDUCATION],
    experience=[
        ExperienceRole(
            organization="Estudio Tributario Canarias",
            title="Full stack AI Developer - Freelance",
            period="2025 - PRESENT",
            bullets=[
                "Development of responsive frontend interfaces using React and Material UI.",
                "Consolidation of two Joomla websites into a brand new single WordPress site.",
                "Implementation of a two-service RAG platform consisting of an Orchestrator and RAG Agents.",
                "WordPress integration with RAG agentic system.",
                "Documentation site with Astro framework and Docusaurus.",
            ],
        ),
        ExperienceRole(
            organization="Sopra Steria Norway",
            title="Senior Software Engineer",
            period="2022 - 2024",
            bullets=[
                "Analysis of existing ITSM processes and integrations finding replacement solutions.",
                "Design and development and test of tailored applications.",
                "Automating and streamlining manual processes with Flow Designer and Integration Hub.",
                "Implementation of custom integrations between ServiceNow and other systems for seamless data flow.",
                "Migration of existing data.",
                "Implementation of security measures for better access management.",
                "Design and development of mobile applications in ServiceNow.",
                "Training and continuous support for customers.",
            ],
        ),
        ExperienceRole(
            organization="Posten Norway",
            title="System developer",
            period="2019 - 2021",
            bullets=[
                "Collaborate with Digdir and other partners to build new functionalities for the interface.",
                "Shaping the team strategy and OKRs, as well as participation in workshops for product development.",
                "Development of the front-end with Javascript, React and Redux as the main technologies.",
                "Development of backend systems in Java and extension of existing APIs.",
                "Writing automated tests both on the front and backend.",
                "Reduction of technical debt by re-writing outdated parts of the code.",
                "Using collaboration tools like Miro, Figma, Slack and GitHub.",
                "Experience with Azure Cosmos DB, Jupyter Notebooks, Kibana and Grafana for monitoring.",
                "Third line support.",
            ],
        ),
        ExperienceRole(
            organization="S5 Consulting",
            title="SAP and Neptune consultant",
            period="2018 - 2019",
            bullets=[
                "Development of internal applications and projects on the SAP Cloud platform.",
                "Integrate between the backend systems and the SAP Cloud platform.",
                "Development of applications on Neptune's Planet 9 platform.",
                "Building applications to demonstrate the capabilities of Disruptive Technologies IoT products.",
                "Exploration of new technologies and execution of proof of concept projects.",
            ],
        ),
        ExperienceRole(
            organization="Diggit Norge",
            title="Frontend developer",
            period="2018",
            bullets=[
                "Build an e-learning platform, where I collaborated interdisciplinary with the product and design teams.",
                "Front End development with React and Redux as the main technologies used.",
                "Integrating with the .NET REST API.",
            ],
        ),
    ],
)

_AI = CvFields(
    name="Manuel Alonso",
    headline="AI Developer",
    summary=(
        "Applied AI developer with 8+ years of software development experience and one year "
        "shipping a production RAG platform. Hands-on with an orchestrator and CrewAI agents, "
        "LLM APIs, Python services, and React interfaces, including an earlier conversation "
        "application using ChatGPT and Google Speech Recognition.\n\n"
        "Focused on delivering end-to-end applied AI solutions — from document analysis and "
        "agent workflows to user-facing tools and cloud deployments. Self-directed and "
        "collaborative, with a track record of turning complex business requirements into "
        "maintainable production systems."
    ),
    skills=[
        "RAG platforms, CrewAI agents and LLM APIs",
        "Python, FastAPI and REST integrations",
        "React, TypeScript and frontend interfaces",
        "Docker, Kubernetes and GCP",
        "Technical design, testing and documentation",
        "Agile delivery and cross-functional collaboration",
    ],
    languages=list(_SHARED_LANGUAGES),
    contact=_SHARED_CONTACT.model_copy(
        update={"email": "manuelalonsoper@gmail.com"}
    ),
    education=[item.model_copy() for item in _SHARED_EDUCATION],
    experience=[
        ExperienceRole(
            organization="Estudio Tributario Canarias",
            title="Full stack AI Developer - Freelance",
            period="2025 - PRESENT",
            bullets=[
                "Implementation of a two-service RAG platform with an Orchestrator and CrewAI-based agents.",
                "Integration of AI-powered document analysis, rule extraction, and scenario summaries.",
                "Development of an agentic planning and pricing interface for legal automations using React, TypeScript, and FastAPI.",
                "Integration of WordPress with the RAG agent system and consolidation of two Joomla websites.",
                "Development of the production Pricing Wizard SaaS platform using Python, Node.js, FastAPI, React, TypeScript, and Tailwind CSS.",
                "Development of responsive frontend applications using React, TypeScript, and Material UI.",
                "Implementation of role-based admin and customer portals with Supabase authentication.",
                "Development of an Express.js and PostgreSQL API for pricing rules, scenarios, and integrations.",
                "Deployment and testing using Docker, Kubernetes, GCP, Railway, Netlify, and Playwright.",
            ],
        ),
        ExperienceRole(
            organization="Sopra Steria Norway",
            title="Senior Software Engineer",
            period="2022 - 2024",
            bullets=[
                "Development of a live conversation application for a Pepper robot in Python.",
                "Integration with ChatGPT and Google Speech Recognition.",
                "Analysis of existing ITSM processes and integrations to design effective replacement solutions.",
                "Design, development, and testing of tailored ServiceNow applications across ITSM, CSM, and FSM.",
                "Automation of manual processes using Flow Designer, Integration Hub, and JavaScript.",
                "Implementation of REST integrations between ServiceNow, Azure AD, and other external systems.",
                "Development of user interfaces, Service Portal widgets, and ServiceNow mobile applications.",
                "Automated testing, technical documentation, customer demonstrations, training, and ongoing support.",
            ],
        ),
        ExperienceRole(
            organization="Posten Norway",
            title="System developer",
            period="2019 - 2021",
            bullets=[
                "Collaborate with Digdir and other partners to build new functionalities for the interface.",
                "Shaping the team strategy and OKRs, as well as participation in workshops for product development.",
                "Development of the front-end with Javascript, React and Redux as the main technologies.",
                "Development of backend systems in Java and .NET, extending existing APIs.",
                "Writing automated tests both on the front and backend.",
                "Reduction of technical debt by re-writing outdated parts of the code.",
                "Using collaboration tools like Miro, Figma, Slack and GitHub.",
                "Experience with Azure Cosmos DB, Jupyter Notebooks, Kibana and Grafana for monitoring.",
                "Third line support.",
            ],
        ),
        ExperienceRole(
            organization="S5 Consulting",
            title="SAP and Neptune consultant",
            period="2018",
            bullets=[
                "Development of internal applications and projects on the SAP Cloud platform.",
                "Integrate between the backend systems and the SAP Cloud platform.",
                "Development of applications on Neptune's Planet 9 platform.",
                "Building applications to demonstrate the capabilities of Disruptive Technologies IoT products.",
                "Exploration of new technologies and execution of proof of concept projects.",
            ],
        ),
        ExperienceRole(
            organization="Diggit Norge",
            title="Frontend developer",
            period="2017",
            bullets=[
                "Build an e-learning platform, where I collaborated interdisciplinary with the product and design teams.",
                "Front End development with React and Redux as the main technologies used.",
                "Integrating with the .NET REST API.",
            ],
        ),
    ],
)
