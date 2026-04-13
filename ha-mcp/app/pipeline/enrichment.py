"""Enrichment: web search for company information and public context."""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def search_company_info(company_name: str, job_title: str = "") -> dict[str, Any]:
    """Search for company information using DuckDuckGo (no API key required).

    Returns structured company context to feed into the analysis.
    """
    if not company_name or company_name.strip() == "":
        return {"results": [], "source": "none", "reason": "no_company_name"}

    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS

        ddgs = DDGS()
        results = []

        # Search 1: Company general info
        query_company = f"{company_name} entreprise societe"
        logger.info("Searching: %s", query_company)
        company_results = ddgs.text(query_company, max_results=3)
        for r in company_results:
            results.append({
                "type": "company_info",
                "title": r.get("title", ""),
                "body": r.get("body", ""),
                "url": r.get("href", ""),
            })

        # Search 2: Company + job context (culture, reviews)
        query_culture = f"{company_name} avis salaries culture entreprise"
        logger.info("Searching: %s", query_culture)
        culture_results = ddgs.text(query_culture, max_results=2)
        for r in culture_results:
            results.append({
                "type": "company_culture",
                "title": r.get("title", ""),
                "body": r.get("body", ""),
                "url": r.get("href", ""),
            })

        # Search 3: Company news/actuality
        query_news = f"{company_name} actualite recrutement"
        logger.info("Searching: %s", query_news)
        news_results = ddgs.text(query_news, max_results=2)
        for r in news_results:
            results.append({
                "type": "company_news",
                "title": r.get("title", ""),
                "body": r.get("body", ""),
                "url": r.get("href", ""),
            })

        # Search 4: Sector/industry context if job title provided
        if job_title:
            query_sector = f"{company_name} {job_title} secteur marche"
            logger.info("Searching: %s", query_sector)
            sector_results = ddgs.text(query_sector, max_results=2)
            for r in sector_results:
                results.append({
                    "type": "sector_context",
                    "title": r.get("title", ""),
                    "body": r.get("body", ""),
                    "url": r.get("href", ""),
                })

        logger.info("Company enrichment: %d results for '%s'", len(results), company_name)

        return {
            "company_name": company_name,
            "results": results,
            "result_count": len(results),
            "source": "duckduckgo",
        }

    except ImportError:
        logger.warning("duckduckgo-search not installed, skipping company enrichment")
        return {
            "company_name": company_name,
            "results": [],
            "source": "unavailable",
            "reason": "duckduckgo-search package not installed",
        }

    except Exception as e:
        logger.error("Company search failed: %s", e)
        return {
            "company_name": company_name,
            "results": [],
            "source": "error",
            "reason": str(e),
        }
