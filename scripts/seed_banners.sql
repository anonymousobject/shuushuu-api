-- Seed initial banners (idempotent - safe to run multiple times)
-- Run with: mariadb -u USER -p DATABASE < scripts/seed_banners.sql
-- Or via docker: docker compose exec mariadb mariadb -u shuushuu_dev -pdev_password_insecure shuushuu_dev < scripts/seed_banners.sql

INSERT INTO banners (name, author, size, full_image, supports_dark, supports_light, active) VALUES

-- whitekitten banners
('', 'whitekitten', 'small', 'whitekitten-polka-small.png', TRUE, TRUE, TRUE),
('', 'whitekitten', 'large', 'whitekitten-neon-large.png', TRUE, TRUE, TRUE),
('', 'whitekitten', 'small', 'whitekitten-neon-small.png', TRUE, TRUE, TRUE),
('mystical', 'whitekitten', 'large', 'large-mystical-light.png', FALSE, TRUE, TRUE),
('artistic', 'whitekitten', 'large', 'large-artistic.png', TRUE, TRUE, TRUE),
('starschibi', 'whitekitten', 'small', 'small-starschibi-light.png', FALSE, TRUE, TRUE),
('butterfly', 'whitekitten', 'large', 'large-butterfly-light.png', FALSE, TRUE, TRUE),
('cake', 'whitekitten', 'large', 'large-cake-light.png', FALSE, TRUE, TRUE),
('cutecutecute', 'whitekitten', 'large', 'large-cutecutecute-light.png', FALSE, TRUE, TRUE),
('moe', 'whitekitten', 'large', 'large-moe-light.png', FALSE, TRUE, TRUE),
('sleeping', 'whitekitten', 'large', 'large-sleeping-light.png', FALSE, TRUE, TRUE),
('teacup', 'whitekitten', 'large', 'large-teacup.png', TRUE, TRUE, TRUE),
('yellow', 'whitekitten', 'large', 'large-yellow.png', TRUE, TRUE, TRUE),

('a lovely place', 'Aizawa', 'small', 'small-a_lovely_place_2.png', TRUE, FALSE, TRUE),
('homuhomu', 'Aizawa', 'large', 'large-homuhomu.png', TRUE, TRUE, TRUE),
('Seeu', 'Aizawa', 'large', 'large-seeu.png', TRUE, TRUE, TRUE)


ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    author = VALUES(author),
    size = VALUES(size),
    supports_dark = VALUES(supports_dark),
    supports_light = VALUES(supports_light),
    active = VALUES(active)
;

-- Verify
SELECT banner_id, name, author, size, supports_dark, supports_light, active FROM banners;
