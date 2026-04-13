"""LLM Client: interface with Claude API for structuration, analysis, and generation."""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _get_client(api_keys: dict[str, str]):
    """Create an Anthropic client with the stored API key."""
    api_key = api_keys.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY non configuree. "
            "Ajoutez votre cle dans l'onglet 'Cles API' du dashboard."
        )

    import anthropic
    return anthropic.Anthropic(api_key=api_key)


def call_claude(
    api_keys: dict[str, str],
    system_prompt: str,
    user_prompt: str,
    expect_json: bool = True,
    model: str = "claude-sonnet-4-20250514",
    max_tokens: int = 8192,
) -> dict[str, Any] | str:
    """Call Claude API and return the response.

    Args:
        api_keys: Dict of API keys (must contain ANTHROPIC_API_KEY)
        system_prompt: System instructions
        user_prompt: User message
        expect_json: If True, parse the response as JSON
        model: Model ID to use
        max_tokens: Maximum tokens in response
    """
    client = _get_client(api_keys)

    logger.info("Calling Claude (%s), prompt length: %d chars", model, len(user_prompt))

    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )

    response_text = message.content[0].text
    logger.info("Claude response: %d chars, stop_reason=%s", len(response_text), message.stop_reason)

    if expect_json:
        # Extract JSON from response (handle markdown code blocks)
        cleaned = response_text.strip()
        if cleaned.startswith("```"):
            # Remove ```json ... ``` wrapper
            lines = cleaned.split("\n")
            start = 1
            end = len(lines) - 1
            if lines[-1].strip() == "```":
                cleaned = "\n".join(lines[start:end])
            else:
                cleaned = "\n".join(lines[start:])
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("Failed to parse Claude response as JSON, returning raw text")
            return {"raw_response": response_text, "parse_error": True}

    return response_text


def structure_job_offer(api_keys: dict[str, str], raw_text: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Use Claude to structure a job offer from raw text."""
    job_properties = json.dumps(
        schema.get("properties", {}).get("job", {}).get("properties", {}),
        indent=2, ensure_ascii=False,
    )

    system = (
        "Tu es un expert en analyse d'offres d'emploi. Tu extrais et structures "
        "les informations d'une offre d'emploi en JSON strict. "
        "Reponds UNIQUEMENT avec le JSON, sans texte autour."
    )

    prompt = f"""Analyse cette offre d'emploi et structure-la selon ce schema JSON.

SCHEMA ATTENDU (proprietes du champ "job"):
{job_properties}

TEXTE DE L'OFFRE:
{raw_text}

Reponds avec un objet JSON contenant uniquement le champ "job" avec les donnees extraites.
Pour les skills, indique le niveau (junior/intermediate/senior/expert/unspecified) et la priorite (must_have/nice_to_have/unspecified).
Si une information n'est pas presente, omets le champ ou utilise une valeur vide."""

    result = call_claude(api_keys, system, prompt)
    if isinstance(result, dict) and "job" in result:
        return result["job"]
    if isinstance(result, dict):
        return result
    return {}


def structure_candidate_cv(api_keys: dict[str, str], raw_text: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Use Claude to structure a CV from raw text."""
    candidate_properties = json.dumps(
        schema.get("properties", {}).get("candidate", {}).get("properties", {}),
        indent=2, ensure_ascii=False,
    )

    system = (
        "Tu es un expert en analyse de CV. Tu extrais et structures "
        "les informations d'un CV en JSON strict. "
        "Reponds UNIQUEMENT avec le JSON, sans texte autour."
    )

    prompt = f"""Analyse ce CV et structure-le selon ce schema JSON.

SCHEMA ATTENDU (proprietes du champ "candidate"):
{candidate_properties}

TEXTE DU CV:
{raw_text}

Reponds avec un objet JSON contenant uniquement le champ "candidate" avec les donnees extraites.
Pour chaque skill, evalue le niveau (beginner/intermediate/advanced/expert) en te basant sur l'experience.
Calcule total_experience_years a partir des experiences listees.
Si une information n'est pas presente, omets le champ."""

    result = call_claude(api_keys, system, prompt)
    if isinstance(result, dict) and "candidate" in result:
        return result["candidate"]
    if isinstance(result, dict):
        return result
    return {}


def analyze_candidacy(
    api_keys: dict[str, str],
    job_data: dict[str, Any],
    candidate_data: dict[str, Any],
    company_info: dict[str, Any] | None,
    schema: dict[str, Any],
) -> dict[str, Any]:
    """Use Claude to analyze candidate-job alignment."""
    analysis_properties = json.dumps(
        schema.get("properties", {}).get("analysis", {}).get("properties", {}),
        indent=2, ensure_ascii=False,
    )

    company_context = ""
    if company_info and company_info.get("results"):
        company_context = f"""
CONTEXTE ENTREPRISE (recherche web):
{json.dumps(company_info, indent=2, ensure_ascii=False)}
"""

    system = (
        "Tu es un expert en recrutement et analyse de candidatures. "
        "Tu compares methodiquement un candidat avec une offre d'emploi. "
        "Tu identifies les alignements, ecarts, signaux et incertitudes. "
        "Reponds UNIQUEMENT avec le JSON, sans texte autour."
    )

    prompt = f"""Analyse l'adequation entre ce candidat et cette offre d'emploi.

OFFRE D'EMPLOI:
{json.dumps(job_data, indent=2, ensure_ascii=False)}

CANDIDAT:
{json.dumps(candidate_data, indent=2, ensure_ascii=False)}
{company_context}
SCHEMA DE SORTIE ATTENDU (proprietes du champ "analysis"):
{analysis_properties}

Instructions:
1. Pour chaque requirement de l'offre, evalue l'alignement du candidat (aligned/partial/gap/exceeded) avec un score 0.0-1.0
2. Identifie les gaps avec leur severite (critical/significant/minor) et le potentiel de remediation
3. Identifie les signaux: forces, risques, opportunites
4. Liste les incertitudes (informations manquantes ou ambigues)
5. Calcule un overall_score pondere (les must_have pesent plus)
6. Donne une recommandation: strong_match/good_match/partial_match/weak_match/no_match
7. Classe les priorites par rang

Reponds avec un objet JSON contenant le champ "analysis"."""

    result = call_claude(api_keys, system, prompt, max_tokens=12000)
    if isinstance(result, dict) and "analysis" in result:
        return result["analysis"]
    if isinstance(result, dict):
        return result
    return {}


def generate_report(
    api_keys: dict[str, str],
    job_data: dict[str, Any],
    candidate_data: dict[str, Any],
    analysis_data: dict[str, Any],
    company_info: dict[str, Any] | None,
) -> str:
    """Use Claude to generate a comprehensive markdown report."""
    company_section = ""
    if company_info and company_info.get("results"):
        company_section = f"""
## Contexte Entreprise
Informations recueillies sur l'entreprise:
{json.dumps(company_info.get('results', [])[:3], indent=2, ensure_ascii=False)}
"""

    system = (
        "Tu es un expert en recrutement. Tu rediges des rapports d'analyse "
        "de candidature clairs, structures et actionnables en markdown."
    )

    prompt = f"""Genere un rapport complet d'analyse de candidature en markdown.

OFFRE D'EMPLOI:
{json.dumps(job_data, indent=2, ensure_ascii=False)}

CANDIDAT:
{json.dumps(candidate_data, indent=2, ensure_ascii=False)}

ANALYSE:
{json.dumps(analysis_data, indent=2, ensure_ascii=False)}

Structure du rapport:
1. **Resume Executif** — Nom candidat, poste, score, recommandation, top 3 forces et top 3 points d'attention
2. **Matrice de Competences** — Tableau comparant les competences requises vs demontrees
3. **Evaluation de l'Experience** — Pertinence des postes precedents, trajectoire
4. **Analyse des Ecarts** — Gaps critiques, significatifs, mineurs avec potentiel de remediation
5. **Signaux & Opportunites** — Forces a valoriser, risques a investiguer
6. **Incertitudes** — Points a verifier, questions d'entretien suggerees
7. **Recommandation & Prochaines Etapes** — Decision argumentee et actions concretes
{company_section}
Redige en francais, sois precis et factuel."""

    return call_claude(api_keys, system, prompt, expect_json=False, max_tokens=12000)
