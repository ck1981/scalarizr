
param (
    [string]$URL = $(throw "Specify package URL to install")
)

$DebugPreference = "Continue"
$ErrorActionPreference = "Stop"

function TmpName () {
param (
    [string]$Suffix
)
    return [System.IO.Path]::GetTempPath() + [System.Guid]::NewGuid().ToString() + $Suffix
}

$InstallDir = "C:\\Program Files\\Scalarizr"
$RunCompletedFile = "$InstallDir\\var\\run\\win-update.completed"
$LogFile = "$InstallDir\\var\\log\\scalarizr_update.log"
$TmpLogFile = TmpName -Suffix ".log"
$BackupDir = ""


function Log() {
param (
    $Message
)
    #Add-Content $LogFile "$Message" 
    Write-Debug $Message
}

function Get-SzrVersion {
param (
    [string]$Path = $InstallDir
)
    return $(Get-Content "$Path\\src\\scalarizr\\version").Trim()
}

function Download-SzrPackage {
param ($URL)

    $WC = New-Object System.Net.WebClient
    $Dst = TmpName -Suffix ".exe"
    Log "Downloading $url"
    $WC.DownloadFile($URL, $Dst)
    Log "Saved to $Dst"
    return $Dst
}

function Create-SzrBackup {
    if (Test-Path $InstallDir) {
        Log "Backuping current installation $(Get-SzrVersion)"
        Rename-Item $InstallDir $BackupDir
    }
}

function Restore-SzrBackup {
    if (Test-Path $BackupDir) {
        Log "Restoring previous installation from backup"
        Rename-Item $BackupDir $InstallDir
    }
}

function Delete-SzrBackup {
    if (Test-Path $BackupDir) {
        Log "Cleanuping"
        Remove-Item -Recurse -Force $BackupDir
    }
}

function Install-SzrPackage {
param ($PackageFile)
    Log "Starting installer"
    $Proc = Start-Process -Wait $PackageFile /S
    if ($Proc.ExitCode) {
        Throw "Scalarizr installer $(Split-Path -Leaf $PackageFile) exited with code: $($Proc.ExitCode)"
    }
    Log "Installer completed"
}

function Stop-SzrService {
param ($Name)
    Log "Stopping $Name"
    $Job = Start-Job {
        Stop-Service $Name
    }
    Wait-Job -Id $Job.Id -Timeout 30
    $Svs = Get-WmiObject -Class Win32_Service -Filter "Name = '$Name'"
    if ($Svs -and $Svs.ProcessId) {
        Stop-Process -Id $Svs.Processid -Force -ErrorAction Stop
    }
}

function Stop-SzrServices {
    Log "Stopping services"
    Stop-SzrService "ScalrUpdClient" 
    Stop-SzrService "Scalarizr"
}

function Start-SzrServices {
param ($Certainly = $false)

    if ($Certainly -or (Get-Service "ScalrUpdClient" -ErrorAction SilentlyContinue)) {
        Log "Starting services"
        Start-Service "ScalrUpdClient"  
        Start-Service "Scalarizr"
    }
}

function Main-Szr {
    if (Test-Path $RunCompletedFile) {
        Remove-Item $RunCompletedFile
    }
    if (Test-Path $InstallDir) {
        $script:BackupDir = $InstallDir + $(Get-SzrVersion)
    }
    $PackageFile = Download-SzrPackage $URL
    Stop-SzrServices
    try {
        Create-SzrBackup
        try {
            Install-SzrPackage $PackageFile
            Start-SzrServices -Сertainly
            Echo $Null > $RunCompletedFile
        }
        catch {
            Write-Error $_ -ErrorAction Continue
            Restore-SzrBackup
        }
        finally {
            #Delete-SzrBackup
        }
    }
    catch {
        Write-Error $_ -ErrorAction Continue
    }
    finally {
        Start-SzrServices -ErrorAction Continue
        Remove-Item $PackageFile
    }
}

#Main-Szr -ErrorAction Continue 2>&1 5>&1
Main-Szr -ErrorAction Continue 2>&1 5>&1 | Tee-Object $TmpLogFile -Append

Get-Content $TmpLogFile | Out-File $LogFile -NoClobber -Append
Remove-Item $TmpLogFile
