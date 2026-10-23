-- Public / semi-public / private classification per premises (RVO terminology).
ALTER TABLE premises_scores ADD COLUMN IF NOT EXISTS access_class text;
