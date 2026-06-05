PRAGMA foreign_keys = ON;

-- Core youth profile table used for analytics and recommendation context.
CREATE TABLE IF NOT EXISTS youth_profiles (
    youth_id TEXT PRIMARY KEY,
    age INTEGER NOT NULL CHECK (age BETWEEN 0 AND 120),
    county TEXT NOT NULL,
    education TEXT NOT NULL,
    employment TEXT NOT NULL,
    housing TEXT NOT NULL,
    mentor_status TEXT NOT NULL,
    placement_count INTEGER NOT NULL CHECK (placement_count >= 0),
    prior_homelessness TEXT NOT NULL CHECK (prior_homelessness IN ('Yes', 'No', 'Unknown')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Master resource catalog for referrals and recommendations.
CREATE TABLE IF NOT EXISTS resources (
    resource_id TEXT PRIMARY KEY,
    resource_name TEXT NOT NULL,
    category TEXT NOT NULL,
    need_tags TEXT NOT NULL,
    service_area TEXT NOT NULL,
    county TEXT NOT NULL,
    city TEXT NOT NULL,
    state TEXT NOT NULL,
    eligibility_age_min INTEGER NOT NULL CHECK (eligibility_age_min BETWEEN 0 AND 120),
    eligibility_age_max INTEGER NOT NULL CHECK (eligibility_age_max BETWEEN 0 AND 120),
    description TEXT NOT NULL,
    referral_method TEXT NOT NULL,
    contact_phone TEXT,
    contact_email TEXT,
    website TEXT,
    ai_match_rules TEXT,
    default_priority TEXT NOT NULL CHECK (default_priority IN ('High', 'Medium', 'Low')),
    caseworker_notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (eligibility_age_min <= eligibility_age_max)
);

-- Snapshot table for risk model outputs over time.
CREATE TABLE IF NOT EXISTS risk_scores (
    risk_score_id INTEGER PRIMARY KEY AUTOINCREMENT,
    youth_id TEXT NOT NULL,
    model_name TEXT NOT NULL,
    model_version TEXT,
    overall_risk_score REAL NOT NULL CHECK (overall_risk_score >= 0.0 AND overall_risk_score <= 1.0),
    housing_risk_score REAL CHECK (housing_risk_score >= 0.0 AND housing_risk_score <= 1.0),
    employment_risk_score REAL CHECK (employment_risk_score >= 0.0 AND employment_risk_score <= 1.0),
    education_risk_score REAL CHECK (education_risk_score >= 0.0 AND education_risk_score <= 1.0),
    risk_level TEXT NOT NULL CHECK (risk_level IN ('Low', 'Medium', 'High')),
    risk_factors_json TEXT,
    calculated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (youth_id) REFERENCES youth_profiles(youth_id) ON DELETE CASCADE
);

-- Intake session metadata for AI assistant conversations.
CREATE TABLE IF NOT EXISTS intake_sessions (
    intake_session_id TEXT PRIMARY KEY,
    youth_id TEXT,
    candidate_profile_id TEXT,
    profile_type TEXT NOT NULL CHECK (profile_type IN ('youth', 'candidate')),
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    session_status TEXT NOT NULL CHECK (session_status IN ('in_progress', 'completed', 'abandoned')),
    assistant_version TEXT,
    channel TEXT,
    top_need_category TEXT,
    FOREIGN KEY (youth_id) REFERENCES youth_profiles(youth_id) ON DELETE CASCADE,
    CHECK (
        (profile_type = 'youth' AND youth_id IS NOT NULL AND candidate_profile_id IS NULL)
        OR
        (profile_type = 'candidate' AND candidate_profile_id IS NOT NULL AND youth_id IS NULL)
    )
);

-- Individual question/answer pairs collected during intake.
CREATE TABLE IF NOT EXISTS intake_answers (
    intake_answer_id INTEGER PRIMARY KEY AUTOINCREMENT,
    intake_session_id TEXT NOT NULL,
    question_key TEXT NOT NULL,
    question_text TEXT,
    answer_value TEXT,
    answer_type TEXT,
    answered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (intake_session_id) REFERENCES intake_sessions(intake_session_id) ON DELETE CASCADE,
    UNIQUE (intake_session_id, question_key)
);

-- AI and rule-based recommendations generated for each youth.
CREATE TABLE IF NOT EXISTS recommendations (
    recommendation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    youth_id TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    risk_score_id INTEGER,
    intake_session_id TEXT,
    match_score REAL,
    priority_rank INTEGER,
    recommendation_reason TEXT,
    recommendation_source TEXT NOT NULL DEFAULT 'ai_matcher',
    recommendation_status TEXT NOT NULL DEFAULT 'proposed' CHECK (
        recommendation_status IN ('proposed', 'reviewed', 'accepted', 'rejected')
    ),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (youth_id) REFERENCES youth_profiles(youth_id) ON DELETE CASCADE,
    FOREIGN KEY (resource_id) REFERENCES resources(resource_id) ON DELETE RESTRICT,
    FOREIGN KEY (risk_score_id) REFERENCES risk_scores(risk_score_id) ON DELETE SET NULL,
    FOREIGN KEY (intake_session_id) REFERENCES intake_sessions(intake_session_id) ON DELETE SET NULL,
    UNIQUE (youth_id, resource_id, intake_session_id)
);

-- Caseworker assignment decisions and follow-up tracking.
CREATE TABLE IF NOT EXISTS assigned_resources (
    assignment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    youth_id TEXT,
    candidate_profile_id TEXT,
    profile_type TEXT NOT NULL CHECK (profile_type IN ('youth', 'candidate')),
    resource_id TEXT NOT NULL,
    intake_session_id TEXT,
    recommendation_id INTEGER,
    assigned_by TEXT,
    priority_level TEXT NOT NULL CHECK (priority_level IN ('High', 'Medium', 'Low')),
    match_score REAL,
    match_reason TEXT,
    assignment_status TEXT NOT NULL DEFAULT 'assigned' CHECK (
        assignment_status IN ('assigned', 'in_progress', 'completed', 'declined', 'closed')
    ),
    assigned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    follow_up_date TEXT,
    notes TEXT,
    FOREIGN KEY (youth_id) REFERENCES youth_profiles(youth_id) ON DELETE CASCADE,
    FOREIGN KEY (resource_id) REFERENCES resources(resource_id) ON DELETE RESTRICT,
    FOREIGN KEY (recommendation_id) REFERENCES recommendations(recommendation_id) ON DELETE SET NULL,
    FOREIGN KEY (intake_session_id) REFERENCES intake_sessions(intake_session_id) ON DELETE SET NULL,
    CHECK (
        (profile_type = 'youth' AND youth_id IS NOT NULL AND candidate_profile_id IS NULL)
        OR
        (profile_type = 'candidate' AND candidate_profile_id IS NOT NULL AND youth_id IS NULL)
    ),
    UNIQUE (intake_session_id, resource_id)
);

CREATE INDEX IF NOT EXISTS idx_youth_profiles_county ON youth_profiles (county);
CREATE INDEX IF NOT EXISTS idx_resources_county ON resources (county);
CREATE INDEX IF NOT EXISTS idx_resources_category ON resources (category);
CREATE INDEX IF NOT EXISTS idx_risk_scores_youth_id ON risk_scores (youth_id);
CREATE INDEX IF NOT EXISTS idx_risk_scores_calculated_at ON risk_scores (calculated_at);
CREATE INDEX IF NOT EXISTS idx_intake_sessions_youth_id ON intake_sessions (youth_id);
CREATE INDEX IF NOT EXISTS idx_intake_sessions_candidate_profile_id ON intake_sessions (candidate_profile_id);
CREATE INDEX IF NOT EXISTS idx_intake_answers_session_id ON intake_answers (intake_session_id);
CREATE INDEX IF NOT EXISTS idx_recommendations_youth_id ON recommendations (youth_id);
CREATE INDEX IF NOT EXISTS idx_recommendations_resource_id ON recommendations (resource_id);
CREATE INDEX IF NOT EXISTS idx_assigned_resources_youth_id ON assigned_resources (youth_id);
CREATE INDEX IF NOT EXISTS idx_assigned_resources_candidate_profile_id ON assigned_resources (candidate_profile_id);
CREATE INDEX IF NOT EXISTS idx_assigned_resources_intake_session_id ON assigned_resources (intake_session_id);
CREATE INDEX IF NOT EXISTS idx_assigned_resources_resource_id ON assigned_resources (resource_id);