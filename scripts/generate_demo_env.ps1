$bytes = New-Object byte[] 32
$generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()

function New-SecureToken {
    $generator.GetBytes($bytes)
    return (($bytes | ForEach-Object { $_.ToString("x2") }) -join "")
}

$erpToken = New-SecureToken
$approverToken = New-SecureToken
$operatorToken = New-SecureToken
$crawlerToken = New-SecureToken

@"
LLM_PROVIDER=offline
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-pro
ERP_SERVICE_TOKEN=$erpToken
APPROVER_API_KEY=$approverToken
OPERATOR_API_KEY=$operatorToken
CRAWLER_SERVICE_TOKEN=$crawlerToken
DEMO_OPERATOR_API_KEY=$operatorToken
DEMO_APPROVER_API_KEY=$approverToken
"@ | Set-Content -Encoding utf8 .env

$generator.Dispose()
Write-Host "Generated local .env. Demo credentials will be loaded into masked UI controls."
