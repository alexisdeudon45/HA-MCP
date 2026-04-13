# Prompt: Structuration Phase

## Job Offer Structuration

Given the raw text of a job offer, extract and structure the following according to the job schema:

- **title**: The job title
- **company**: Name, sector, location, size
- **contract**: Type (CDI/CDD/freelance), duration, start date, remote policy
- **requirements**:
  - required_skills: List with skill name, level, years, priority (must_have/nice_to_have)
  - required_education: Degree, field, level
  - required_languages: Language and level
  - experience_years: Minimum and preferred
- **responsibilities**: List of key responsibilities
- **compensation**: Salary range, benefits

## CV Structuration

Given the raw text of a CV, extract and structure the following according to the candidate schema:

- **identity**: Name, title, location, contact info
- **skills**: Each with category, level, years, evidence from experience
- **experience**: Each role with company, dates, duration, description, achievements, technologies
- **education**: Degree, field, institution, year
- **languages**: Language and level
- **certifications**: Name, issuer, year
- **total_experience_years**: Calculated from experience entries

## Rules

- If information is not present, omit the field or use empty values
- Set confidence scores based on clarity of the source text
- Mark ambiguous extractions in uncertainties
