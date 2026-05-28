param(
    [string]$UserId = "a3b4159c-6345-4af2-aa44-5e29c701379b"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$sql = @"
update user_profiles
set injuries = '[]'::jsonb
where user_id = '$UserId'
  and injuries ? 'shoulder';

insert into long_term_memories (
    id,
    user_id,
    memory_type,
    content,
    importance,
    source,
    memory_metadata,
    confidence,
    status,
    access_count
)
select
    gen_random_uuid(),
    '$UserId',
    'correction',
    'User explicitly denied a right shoulder injury. The previous shoulder injury tag was a false extraction and has been removed from the canonical profile.',
    0.92,
    'manual_correction',
    '{"field":"injuries","action":"remove","value":"shoulder","reason":"user_denied_false_positive"}'::jsonb,
    0.95,
    'active',
    0
where not exists (
    select 1
    from long_term_memories
    where user_id = '$UserId'
      and memory_type = 'correction'
      and memory_metadata->>'value' = 'shoulder'
      and memory_metadata->>'action' = 'remove'
);

select user_id, injuries
from user_profiles
where user_id = '$UserId';

select memory_type, content, memory_metadata
from long_term_memories
where user_id = '$UserId'
  and memory_type = 'correction'
order by created_at desc
limit 1;
"@

docker compose ps postgres | Out-Host
$sql | docker compose exec -T postgres psql -U fitness -d ai_fitness_agent -v ON_ERROR_STOP=1
