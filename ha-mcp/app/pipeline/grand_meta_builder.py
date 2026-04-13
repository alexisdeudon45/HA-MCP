"""Grand Meta Builder: uses Claude to build the comprehensive 7-category grand meta-schema object."""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from .llm import call_claude

logger = logging.getLogger(__name__)


def build_grand_meta(
    api_keys: dict[str, str],
    job_data: dict[str, Any],
    candidate_data: dict[str, Any],
    analysis_data: dict[str, Any],
    resources: list[dict[str, Any]],
    company_info: dict[str, Any],
) -> dict[str, Any]:
    """Build the full grand meta-schema object using Claude for deep inference.

    Calls Claude multiple times, one per category, to maximize depth and quality.
    """
    resource_context = _build_resource_context(resources)
    company_context = _build_company_context(company_info)

    # Build each category with dedicated Claude calls
    entity = _build_entity(api_keys, job_data, company_context, resource_context)
    organigramme = _build_organigramme(api_keys, job_data, entity)
    real_expectations = _build_real_expectations(api_keys, job_data, entity, organigramme, resource_context)
    job_position = _build_job_position(api_keys, job_data)
    candidate_profile = _build_candidate_profile(api_keys, candidate_data, resource_context)
    historical = _build_historical(api_keys, job_data, entity, resource_context)
    match_synthesis = _build_match_synthesis(
        api_keys, job_position, candidate_profile, entity,
        real_expectations, historical, analysis_data
    )

    return {
        "entity": entity,
        "organigramme": organigramme,
        "real_expectations": real_expectations,
        "job_position": job_position,
        "candidate_profile": candidate_profile,
        "historical": historical,
        "match_synthesis": match_synthesis,
    }


def _call_category(api_keys: dict[str, str], category: str, prompt: str) -> dict[str, Any]:
    """Call Claude for a specific grand meta category."""
    system = (
        f"Tu es un expert en recrutement et analyse organisationnelle. "
        f"Tu remplis la categorie '{category}' du grand meta-schema d'analyse de candidature. "
        f"Base-toi sur les faits disponibles. Pour les champs inferres, indique un score de confidence. "
        f"Reponds UNIQUEMENT en JSON strict, sans texte autour."
    )
    result = call_claude(api_keys, system, prompt, model="claude-sonnet-4-20250514", max_tokens=6000)
    if isinstance(result, dict):
        return result
    return {}


def _build_entity(api_keys: dict, job_data: dict, company_ctx: str, resource_ctx: str) -> dict:
    prompt = f"""Remplis la categorie "entity" du grand meta-schema. C'est tout ce qu'on sait sur l'entreprise qui recrute.

DONNEES DE L'OFFRE:
{json.dumps(job_data, indent=2, ensure_ascii=False)}

CONTEXTE ENTREPRISE (recherches web):
{company_ctx}

RESSOURCES ADDITIONNELLES:
{resource_ctx}

Retourne un JSON avec les champs: company (name, legal_name, sector, sub_sector, size, employee_count, revenue, founded_year, headquarters, offices, website, linkedin, stock_ticker), culture (values, management_style, work_life_balance, innovation_level, diversity_inclusion, glassdoor_rating, employee_sentiment, turnover_rate), financial_health (status, recent_funding, profitability, market_position), reputation (brand_strength, employer_brand, recent_news, controversies), technology_stack (main_technologies, cloud_provider, methodology, tech_maturity), confidence, sources.

Infere ce que tu peux, mets confidence entre 0 et 1."""
    return _call_category(api_keys, "entity", prompt)


def _build_organigramme(api_keys: dict, job_data: dict, entity: dict) -> dict:
    prompt = f"""Remplis la categorie "organigramme" — structure hierarchique du poste.

OFFRE:
{json.dumps(job_data, indent=2, ensure_ascii=False)}

ENTITY:
{json.dumps(entity, indent=2, ensure_ascii=False)}

Retourne un JSON avec: department, team, team_size, reporting_to (title, name, seniority), direct_reports, peers, requestor (name, title, department, relation_to_role, hiring_experience), position_in_hierarchy, collaboration_scope, confidence, sources.

Pour le requestor, infere qui a probablement demande l'ouverture du poste et pourquoi."""
    return _call_category(api_keys, "organigramme", prompt)


def _build_real_expectations(api_keys: dict, job_data: dict, entity: dict, organigramme: dict, resource_ctx: str) -> dict:
    prompt = f"""Remplis la categorie "real_expectations" — les attentes REELLES derriere l'offre.

OFFRE:
{json.dumps(job_data, indent=2, ensure_ascii=False)}

ENTITY:
{json.dumps(entity, indent=2, ensure_ascii=False)}

ORGANIGRAMME:
{json.dumps(organigramme, indent=2, ensure_ascii=False)}

RESSOURCES:
{resource_ctx}

Analyse en profondeur:
- hiring_motivation: pourquoi ce poste existe (growth, replacement, restructuring, new_project, strategic_shift, compliance, cost_optimization, talent_upgrade)
- urgency: quel est le niveau d'urgence
- predecessor: y avait-il quelqu'un avant, pourquoi est-il parti
- hidden_requirements: exigences non ecrites mais reelles (culture fit, politique interne, etc.)
- real_challenges: les vrais defis du poste vs ce qui est ecrit
- success_criteria: comment le succes sera mesure (30/60/90 jours)
- growth_potential: perspectives d'evolution
- red_flags: signaux d'alerte detectes dans l'offre ou le contexte

Sois perspicace. Lis entre les lignes."""
    return _call_category(api_keys, "real_expectations", prompt)


def _build_job_position(api_keys: dict, job_data: dict) -> dict:
    prompt = f"""Remplis la categorie "job_position" — analyse detaillee du poste.

DONNEES STRUCTUREES DE L'OFFRE:
{json.dumps(job_data, indent=2, ensure_ascii=False)}

Retourne un JSON complet avec: title, title_normalized, seniority, contract, location, compensation (avec market_comparison), requirements (hard_skills avec priority, soft_skills, education, languages, experience_years_min/preferred, certifications), responsibilities (avec weight et category), tools_and_environment, confidence, sources.

Pour chaque hard_skill, categorise: language, framework, database, cloud, devops, methodology, domain, other.
Pour les responsibilities, estime le % du temps pour chacune."""
    return _call_category(api_keys, "job_position", prompt)


def _build_candidate_profile(api_keys: dict, candidate_data: dict, resource_ctx: str) -> dict:
    prompt = f"""Remplis la categorie "candidate_profile" — vue complete du candidat.

DONNEES STRUCTUREES DU CV:
{json.dumps(candidate_data, indent=2, ensure_ascii=False)}

RESSOURCES ADDITIONNELLES:
{resource_ctx}

Retourne un JSON avec: identity, professional_summary (2-3 phrases), career_trajectory (direction, velocity, specialization, total/relevant experience, companies_count, average/longest/shortest tenure), skills_inventory (avec evidence_strength pour chaque skill), experience (avec relevance_to_job), education (avec institution_ranking si inferable), languages, certifications, soft_skills_observed (infere depuis le parcours), online_presence, confidence, sources.

Pour evidence_strength: strong = demontre par multiple experiences, moderate = une experience, weak = mentionne sans preuve, claimed_only = juste liste."""
    return _call_category(api_keys, "candidate_profile", prompt)


def _build_historical(api_keys: dict, job_data: dict, entity: dict, resource_ctx: str) -> dict:
    prompt = f"""Remplis la categorie "historical" — contexte temporel et historique du recrutement.

OFFRE:
{json.dumps(job_data, indent=2, ensure_ascii=False)}

ENTITY:
{json.dumps(entity, indent=2, ensure_ascii=False)}

RESSOURCES:
{resource_ctx}

Analyse:
- job_posting: est-ce la premiere publication, combien de fois publie, depuis combien de temps, sur quelles plateformes, y a-t-il eu des modifications
- recruitment_history: combien de personnes ont deja occupe ce role, temps moyen de recrutement, difficulte a pourvoir
- market_context: disponibilite des talents, tendance de demande, salaire marche, offres concurrentes
- company_hiring_pattern: volume de recrutement, layoffs recents, nombre de postes ouverts

Infere ce que tu peux depuis le contexte. Sois explicite sur ce qui est factuels vs infere."""
    return _call_category(api_keys, "historical", prompt)


def _build_match_synthesis(
    api_keys: dict, job: dict, candidate: dict,
    entity: dict, expectations: dict, historical: dict,
    analysis: dict,
) -> dict:
    prompt = f"""Remplis la categorie "match_synthesis" — verdict final croise.

JOB:
{json.dumps(job, indent=2, ensure_ascii=False)}

CANDIDATE:
{json.dumps(candidate, indent=2, ensure_ascii=False)}

ENTITY:
{json.dumps(entity, indent=2, ensure_ascii=False)}

REAL EXPECTATIONS:
{json.dumps(expectations, indent=2, ensure_ascii=False)}

HISTORICAL:
{json.dumps(historical, indent=2, ensure_ascii=False)}

ANALYSE PRECEDENTE:
{json.dumps(analysis, indent=2, ensure_ascii=False)}

Retourne:
- overall_score (0-1)
- recommendation: strong_match/good_match/partial_match/weak_match/no_match
- category_scores: hard_skills, soft_skills, experience, education, culture_fit, career_trajectory, compensation_fit, location_fit, growth_potential (chacun 0-1)
- top_strengths (max 5)
- top_risks (max 5)
- top_unknowns (max 5)
- interview_questions: 5-8 questions ciblees avec purpose et targets_category
- decision_factors: facteurs cles avec weight, verdict (go/caution/no_go/unknown), rationale
- next_steps

Le verdict doit etre argumente et actionnable."""
    return _call_category(api_keys, "match_synthesis", prompt)


def _build_resource_context(resources: list[dict]) -> str:
    if not resources:
        return "Aucune ressource disponible."
    lines = []
    for r in resources[:12]:
        summary = r.get("content", {}).get("summary", "")[:200]
        lines.append(f"- [{r.get('type', '?')}] {r.get('name', '?')}: {summary}")
    return "\n".join(lines)


def _build_company_context(company_info: dict) -> str:
    if not company_info or not company_info.get("results"):
        return "Aucune information entreprise disponible."
    lines = []
    for r in company_info.get("results", [])[:8]:
        lines.append(f"- [{r.get('type', '?')}] {r.get('title', '')}: {r.get('body', '')[:200]}")
    return "\n".join(lines)
