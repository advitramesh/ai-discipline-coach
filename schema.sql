-- ============================================================
-- AI Discipline Coach — full schema
-- Run this in the Supabase SQL editor (safe to re-run)
-- ============================================================

-- commitments
CREATE TABLE IF NOT EXISTS commitments (
  id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id    text        NOT NULL,
  title         text        NOT NULL,
  type          text        NOT NULL CHECK (type IN ('do', 'abstain', 'one-time')),
  frequency     text        CHECK (frequency IN ('daily', 'weekly', 'specific_days', 'one-time')),
  days_of_week  text[],
  due_date      date,
  active        boolean     DEFAULT true,
  created_at    timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_commitments_session ON commitments (session_id);
CREATE INDEX IF NOT EXISTS idx_commitments_active  ON commitments (active);

-- commitment_logs
CREATE TABLE IF NOT EXISTS commitment_logs (
  id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  commitment_id   uuid        REFERENCES commitments (id) ON DELETE CASCADE,
  session_id      text        NOT NULL,
  date            date        NOT NULL,
  status          text        NOT NULL CHECK (status IN ('completed', 'skipped', 'lapsed')),
  note            text,
  created_at      timestamptz DEFAULT now(),
  UNIQUE (commitment_id, date)
);

CREATE INDEX IF NOT EXISTS idx_clogs_commitment ON commitment_logs (commitment_id);
CREATE INDEX IF NOT EXISTS idx_clogs_session    ON commitment_logs (session_id);
CREATE INDEX IF NOT EXISTS idx_clogs_date       ON commitment_logs (date);

-- goals
CREATE TABLE IF NOT EXISTS goals (
  id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id  text        NOT NULL,
  title       text        NOT NULL,
  active      boolean     DEFAULT true,
  created_at  timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_goals_session ON goals (session_id);
CREATE INDEX IF NOT EXISTS idx_goals_active  ON goals (active);

-- link commitments → goals (migration-safe)
ALTER TABLE commitments ADD COLUMN IF NOT EXISTS goal_id uuid REFERENCES goals (id) ON DELETE SET NULL;
