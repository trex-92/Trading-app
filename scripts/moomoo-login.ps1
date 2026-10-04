# One-time Moomoo OAuth 2.1 + PKCE login for Windows PowerShell 5.1+ (built in; nothing to install).
# Run:  powershell -ExecutionPolicy Bypass -File scripts\moomoo-login.ps1
# Writes the same token file the bot reads: %USERPROFILE%\.config\trading-bot\moomoo_tokens.json
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Base = if ($env:MOOMOO_BASE) { $env:MOOMOO_BASE } else { 'https://webapi.moomoo.com' }
$Redirect = 'http://localhost:60355/callback'
$TokenFile = if ($env:MOOMOO_TOKEN_FILE) { $env:MOOMOO_TOKEN_FILE } else { Join-Path $env:USERPROFILE '.config\trading-bot\moomoo_tokens.json' }

function ConvertTo-Base64Url([byte[]]$Bytes) {
    [Convert]::ToBase64String($Bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}
function New-RandomString([int]$Count) {
    $b = New-Object byte[] $Count
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
    ConvertTo-Base64Url $b
}
function Invoke-Moomoo([string]$Url, $Body, [switch]$Json) {
    try {
        if ($Json) { Invoke-RestMethod -Method Post -Uri $Url -ContentType 'application/json' -Body ($Body | ConvertTo-Json -Depth 5) }
        else { Invoke-RestMethod -Method Post -Uri $Url -ContentType 'application/x-www-form-urlencoded' -Body $Body }
    } catch {
        $detail = if ($_.ErrorDetails) { $_.ErrorDetails.Message } else { $_.Exception.Message }
        throw "$Url failed: $detail"
    }
}

$existing = if (Test-Path $TokenFile) { Get-Content $TokenFile -Raw | ConvertFrom-Json } else { $null }
$clientId = if ($existing -and $existing.client_id) { $existing.client_id } else { $null }
if (-not $clientId) {
    $clientId = (Invoke-Moomoo "$Base/oauth2/register" @{
        redirect_uris = @($Redirect); token_endpoint_auth_method = 'none'
        grant_types = @('authorization_code', 'refresh_token'); response_types = @('code'); client_name = 'Trading Bot'
    } -Json).client_id
}

$verifier = New-RandomString 48
$sha = [Security.Cryptography.SHA256]::Create()
$challenge = ConvertTo-Base64Url ($sha.ComputeHash([Text.Encoding]::ASCII.GetBytes($verifier)))
$state = New-RandomString 16
$q = @{ client_id = $clientId; code_challenge = $challenge; code_challenge_method = 'S256'
        redirect_uri = $Redirect; response_type = 'code'; state = $state }.GetEnumerator() |
     ForEach-Object { "$($_.Key)=$([Uri]::EscapeDataString($_.Value))" }
$authUrl = "$Base/oauth2/authorize/confirm?" + ($q -join '&')

$listener = New-Object Net.HttpListener
$listener.Prefixes.Add('http://localhost:60355/')
$listener.Start()
Write-Host "`nOpening your browser. If it does not open, paste this URL into it:`n`n$authUrl`n"
try { Start-Process $authUrl } catch { }

$code = $null
$task = $listener.GetContextAsync()
if (-not $task.Wait(300000)) { $listener.Stop(); throw 'Timed out after 5 minutes. Run the script again.' }
$ctx = $task.Result
$params = [Web.HttpUtility]::ParseQueryString($ctx.Request.Url.Query)
if ($ctx.Request.Url.AbsolutePath -eq '/callback' -and $params['state'] -eq $state -and $params['code']) { $code = $params['code'] }
$msg = if ($code) { 'Authorized. You can close this tab and return to PowerShell.' } else { 'Authorization failed. See PowerShell.' }
$buf = [Text.Encoding]::UTF8.GetBytes($msg)
$ctx.Response.ContentType = 'text/plain'
$ctx.Response.OutputStream.Write($buf, 0, $buf.Length)
$ctx.Response.Close()
$listener.Stop()
if (-not $code) { throw "Bad callback: $($ctx.Request.Url.Query)" }

$t = Invoke-Moomoo "$Base/oauth2/token" @{
    grant_type = 'authorization_code'; code = $code; client_id = $clientId
    redirect_uri = $Redirect; code_verifier = $verifier }

$dir = Split-Path $TokenFile
if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
$expiresIn = if ($t.expires_in) { [int]$t.expires_in } else { 7200 }
$nowEpoch = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
$json = @{ client_id = $clientId; access_token = $t.access_token; expires_at = $nowEpoch + $expiresIn
           refresh_token = $t.refresh_token; scope = "$($t.scope)" } | ConvertTo-Json
# Write UTF-8 WITHOUT a BOM (Set-Content -Encoding UTF8 adds one in Windows PowerShell 5.1 and breaks the bot's JSON parser).
[IO.File]::WriteAllText($TokenFile, $json, (New-Object Text.UTF8Encoding($false)))
Write-Host "Saved tokens to $TokenFile`nGranted scope: $($t.scope)"
