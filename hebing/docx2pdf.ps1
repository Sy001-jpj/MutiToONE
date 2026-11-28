param(
    [Parameter(Mandatory=$true)][string]$Src,
    [Parameter(Mandatory=$true)][string]$Dst
)
$ErrorActionPreference = "Stop"
$srcPath = (Resolve-Path -LiteralPath $Src).Path
$dstPath = [System.IO.Path]::GetFullPath($Dst)
$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = 0
try {
    $doc = $word.Documents.Open($srcPath, $false, $true)
    $doc.ExportAsFixedFormat($dstPath, 17)
    $doc.Close(0)
} finally {
    $word.Quit()
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($word) | Out-Null
}
if (Test-Path -LiteralPath $dstPath) { "PDF_OK" } else { "PDF_FAIL" }
