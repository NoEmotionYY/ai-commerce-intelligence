$bytes = New-Object byte[] 32
$generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()

function New-SecureToken {
    $generator.GetBytes($bytes)
    return (($bytes | ForEach-Object { $_.ToString("x2") }) -join "")
}

@"
ERP_SERVICE_TOKEN=$(New-SecureToken)
APPROVER_API_KEY=$(New-SecureToken)
OPERATOR_API_KEY=$(New-SecureToken)
CRAWLER_SERVICE_TOKEN=$(New-SecureToken)
"@ | Set-Content -Encoding utf8 .env

$generator.Dispose()
Write-Host "Generated local .env. Copy operator and approver credentials into the UI."
