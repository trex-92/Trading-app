# Creates .env (Expo app) and bot\.env (trading bot) by asking for your Supabase values.
# Run from the repo root:  powershell -ExecutionPolicy Bypass -File scripts\setup-env.ps1
# Both files are git-ignored. The secret key is typed hidden and only ever written to bot\.env.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$utf8 = New-Object Text.UTF8Encoding($false)

function Read-Plain([string]$Prompt) {
    $s = Read-Host -Prompt $Prompt -AsSecureString
    $p = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)
    try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($p) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($p) }
}
function Set-EnvLine([string]$Text, [string]$Key, [string]$Value) {
    $line = "$Key=$Value"
    if ($Text -match "(?m)^$Key=.*$") { [regex]::Replace($Text, "(?m)^$Key=.*$", { param($m) $line }) }
    else { $Text.TrimEnd() + "`n$line`n" }
}

$url = (Read-Host 'Supabase project URL (https://xxxx.supabase.co)').Trim().TrimEnd('/')
$anon = (Read-Host 'Publishable / anon key (safe for the app)').Trim()
$service = (Read-Plain 'Secret / service_role key (hidden; stays in bot\.env only)').Trim()
$userId = (Read-Host 'Your Supabase user ID (Authentication > Users; sign up in the app first if empty)').Trim()
if (-not $url.StartsWith('https://')) { throw 'URL must start with https://' }

# App .env
$app = Get-Content (Join-Path $root '.env.example') -Raw
$app = Set-EnvLine $app 'EXPO_PUBLIC_SUPABASE_URL' $url
$app = Set-EnvLine $app 'EXPO_PUBLIC_SUPABASE_ANON_KEY' $anon
[IO.File]::WriteAllText((Join-Path $root '.env'), $app, $utf8)

# Bot .env (simulation only; live trading stays locked)
$bot = Get-Content (Join-Path $root 'bot\.env.example') -Raw
$bot = Set-EnvLine $bot 'SUPABASE_URL' $url
$bot = Set-EnvLine $bot 'SUPABASE_SERVICE_ROLE_KEY' $service
$bot = Set-EnvLine $bot 'SUPABASE_USER_ID' $userId
$bot = Set-EnvLine $bot 'BROKER' 'moomoo_rest'
$bot = Set-EnvLine $bot 'TRADE_ENV' 'SIMULATE'
$bot = Set-EnvLine $bot 'ALLOW_LIVE' 'no'
[IO.File]::WriteAllText((Join-Path $root 'bot\.env'), $bot, $utf8)

Write-Host "`nWrote $root\.env and $root\bot\.env (SIMULATE, live trading locked)."
if (-not $userId) { Write-Host 'SUPABASE_USER_ID is empty: fill it in bot\.env after you sign up in the app.' }
