ALTER TABLE wagmi_temp_verifications ADD COLUMN IF NOT EXISTS wallet_address VARCHAR;
ALTER TABLE wagmi_memberships ALTER COLUMN user_id TYPE BIGINT;
ALTER TABLE wagmi_temp_verifications ALTER COLUMN user_id TYPE BIGINT;
