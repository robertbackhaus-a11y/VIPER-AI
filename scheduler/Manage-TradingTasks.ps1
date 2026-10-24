# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

<#
.SYNOPSIS
  Verwaltet die sieben geplanten VIPER-Aufgaben ("Trading-*") des Runtime-Roots (Standard C:\tools\Viper-AI).
  Aufruf ueblicherweise ueber "viper-ai tasks list|deploy|edit|delete".

.DESCRIPTION
  Sieben Aufgaben (Name "Trading-*"). Die vier Backfills laufen bei Anmeldung plus taeglich bzw. woechentlich; Candidate Discovery (17:35)
  und Market Intelligence (08:15 und 17:45) laufen nur zu den festen Zeiten (kein Anmelde-Ausloeser) und haben ein Zeitlimit von 2 Stunden.
  Alle Aufgaben holen verpasste Laeufe nach (StartWhenAvailable) und laufen nie parallel mit sich selbst. Reihenfolge am Abend:
  MarketData 17:15, FX 17:20, EventsNews 17:25, Discovery 17:35, Market Intelligence 17:45. Sonntags: Fundamentals 10:00, danach ETF-Refresh 11:00
  (Refresh-TradingEtfData.py --write; Exitcode ungleich 0 = Anbieter-/Parserfehler oder teilweise Aktualisierung, die alten gueltigen Dateien
  bleiben; kein Wiederholen, keine Folgejobs; Zeitlimit 1 Stunde, kein Anmelde-Ausloeser).

  Alle Aufgaben laufen unsichtbar: Die Aktion ist <Root>\.venv\Scripts\pythonw.exe (ohne Konsole) mit dem Starter Run-TradingTask.py
  <Log> <Skript> <Argumente>; er haengt stdout und stderr an die Logdatei an und gibt den Exitcode des Skripts weiter.

  Uhrzeiten sind Ortszeit (Europe/Berlin, mit Sommer-/Winterzeit): Die Startzeit wird ohne UTC-Versatz gespeichert. Mit Versatz
  (New-ScheduledTaskTrigger -At schreibt z. B. 11:00+02:00) haelt die Aufgabenplanung die UTC-Zeit fest und die Aufgabe laeuft nach der
  Zeitumstellung eine Stunde verschoben.

  Pip-Installation (Produktion seit 22.10.2026): Code unter <Root>\.venv\Lib\site-packages\viper_ai\app, Arbeitsverzeichnis der drei Aufgaben
  mit WorkDir = der Root; Deploy verweigert sie, wenn die Benutzer-Umgebungsvariable VIPER_TRADING_ROOT nicht genau auf -Root zeigt (sonst
  schriebe der Code nach C:\tools\trading). Ohne pip-Installation unter -Root gilt das fruehere Layout (<Root>\.venv und <Root>\app).

  Nur die sieben Namen der Tabelle $jobs werden je angefasst; fremde Aufgaben nie.

.PARAMETER Action
  List    Zeigt Name, aktiviert, Zeitplan, naechsten Lauf und Aktion (Standard, aendert nichts)
  Deploy  Legt fehlende Aufgaben an und aktualisiert vorhandene (Aktion, Einstellungen, Startzeiten als Ortszeit); idempotent. Ein mit Edit
          geaenderter Zeitplan und der Aktiviert-Zustand bleiben erhalten; -ResetSchedule setzt die Standardzeiten der Tabelle.
  Edit    Aendert eine Aufgabe (-Task): -At "HH:MM[,HH:MM]" mit -Day Daily|Monday..Sunday (Standard: bisherige Art), -Enable, -Disable
  Delete  Loescht eine Aufgabe (-Task) oder alle sieben (-All)

.PARAMETER DryRun
  Zeigt nur, was passieren wuerde.
#>
[CmdletBinding()]
param(
    [ValidateSet('List', 'Deploy', 'Edit', 'Delete')]
    [string]$Action = 'List',
    [string]$Task = '',
    [string]$At = '',
    [ValidateSet('', 'Daily', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')]
    [string]$Day = '',
    [switch]$Enable,
    [switch]$Disable,
    [switch]$All,
    [switch]$ResetSchedule,
    [string]$Root = 'C:\tools\Viper-AI',
    [string]$Prefix = 'Trading-',  # nur fuer Tests: eigene Namen statt der produktiven Aufgaben
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$python = Join-Path $Root '.venv\Scripts\python.exe'
$pythonw = Join-Path $Root '.venv\Scripts\pythonw.exe'
$pipCode = Join-Path $Root '.venv\Lib\site-packages\viper_ai\app'
$isPip = Test-Path (Join-Path $pipCode 'Run-TradingTask.py')
$codeDir = if ($isPip) { $pipCode } else { Join-Path $Root 'app' }
$launcher = if ($isPip) { Join-Path $pipCode 'Run-TradingTask.py' } else { Join-Path $Root 'app\Run-TradingTask.py' }
$user = "$env:USERDOMAIN\$env:USERNAME"

# Name, Skript, Argumente, Logdatei, Zeitplan (At: eine oder mehrere Uhrzeiten)
# Optional: Logon = $false (kein Anmelde-Ausloeser), WorkDir (relativ zu Root), LimitHours (Zeitlimit)
$jobs = @(
    @{ Name = 'MarketData-Backfill'; Script = 'Backfill-TradingMarketData.py';      Args = '--write'; Log = 'market-data-backfill.log';      Kind = 'Daily';  At = '17:15' },
    @{ Name = 'FXRates-Backfill';    Script = 'Backfill-TradingFXRatesECB.py';      Args = '--write --quote-currency USD,GBP,AUD,KRW'; Log = 'fx-rates-backfill.log';         Kind = 'Daily';  At = '17:20' },
    @{ Name = 'EventsNews-Backfill'; Script = 'Backfill-TradingEventsNews.py';      Args = '--write'; Log = 'events-news-backfill.log';      Kind = 'Daily';  At = '17:25' },
    @{ Name = 'Fundamentals-SEC';    Script = 'Backfill-TradingFundamentalsSEC.py'; Args = '';        Log = 'fundamentals-sec-backfill.log'; Kind = 'Weekly'; At = '10:00' },
    @{ Name = 'Candidate-Discovery'; Script = 'Discover-TradingCandidates.py';      Args = '';        Log = 'candidate-discovery.log';       Kind = 'Daily';  At = @('17:35'); Logon = $false; WorkDir = 'app'; LimitHours = 2 },
    @{ Name = 'Market-Intelligence'; Script = 'Collect-TradingMarketIntelligence.py'; Args = '';      Log = 'market-intelligence.log';       Kind = 'Daily';  At = @('08:15', '17:45'); Logon = $false; WorkDir = 'app'; LimitHours = 2 },
    @{ Name = 'ETF-Refresh';         Script = 'Refresh-TradingEtfData.py';          Args = '--write'; Log = 'etf-refresh.log';               Kind = 'Weekly'; At = '11:00'; Logon = $false; WorkDir = 'app'; LimitHours = 1 }
)
$dayNames = @{ 1 = 'So'; 2 = 'Mo'; 4 = 'Di'; 8 = 'Mi'; 16 = 'Do'; 32 = 'Fr'; 64 = 'Sa' }

# Die Aktion einer Aufgabe: pythonw.exe (keine Konsole) + Starter + Log + Skript + Argumente; Arbeitsverzeichnis wie bisher
function Get-ActionParams($j) {
    $script = Join-Path $codeDir $j.Script
    if (-not (Test-Path $script)) { throw "Skript fehlt: $script" }
    $log = Join-Path $Root ("logs\" + $j.Log)
    $argLine = ('"{0}" "{1}" "{2}" {3}' -f $launcher, $log, $script, $j.Args).TrimEnd()
    $params = @{ Execute = $pythonw; Argument = $argLine }
    if ($j.WorkDir) { $params.WorkingDirectory = $(if ($isPip) { $Root } else { Join-Path $Root $j.WorkDir }) }
    $params
}

function Assert-SilentRuntime {
    foreach ($file in @($python, $pythonw, $launcher)) { if (-not (Test-Path $file)) { throw "Fehlt: $file" } }
    if ($isPip) {
        $configured = [Environment]::GetEnvironmentVariable('VIPER_TRADING_ROOT', 'User')
        if ($configured -ne $Root) {
            $text = "VIPER_TRADING_ROOT (Benutzer) ist '$configured', erwartet '$Root': die Aufgaben wuerden einen anderen Root benutzen"
            if ($DryRun) { Write-Warning $text } else { throw $text }
        }
    }
}

function Set-LocalStart($trigger) {
    # Startzeit als Ortszeit ohne Versatz ("2026-10-16T11:00:00"): die Aufgabenplanung wertet sie in der Zeitzone des Rechners aus
    # (Europe/Berlin, mit Sommer-/Winterzeit). Mit Versatz ("...+02:00") gilt dagegen die feste UTC-Zeit.
    # Je nach PowerShell-Version kommt die Startzeit mit Versatz ("+02:00") oder in UTC ("Z"); beides wird in die Ortszeit dieses Tages umgerechnet.
    if ($trigger.StartBoundary) {
        $local = [DateTimeOffset]::Parse([string]$trigger.StartBoundary, [Globalization.CultureInfo]::InvariantCulture).ToLocalTime()
        $trigger.StartBoundary = $local.ToString("yyyy-MM-dd'T'HH:mm:ss", [Globalization.CultureInfo]::InvariantCulture)
    }
    return $trigger
}

# Uhrzeit-Ausloeser als Ortszeit: taeglich oder woechentlich an einem Tag (Weekly in der Tabelle = Sonntag)
function New-TimeTriggers([string[]]$times, [string]$kind) {
    foreach ($time in $times) {
        if ($kind -eq 'Daily') { Set-LocalStart (New-ScheduledTaskTrigger -Daily -At $time) }
        elseif ($kind -eq 'Weekly') { Set-LocalStart (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At $time) }
        else { Set-LocalStart (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $kind -At $time) }
    }
}

function New-Settings($j) {
    $settingsParams = @{ StartWhenAvailable = $true; MultipleInstances = 'IgnoreNew' }
    if ($j.LimitHours) { $settingsParams.ExecutionTimeLimit = New-TimeSpan -Hours $j.LimitHours }
    New-ScheduledTaskSettingsSet @settingsParams
}

function Get-Job([string]$taskName) {
    $short = if ($taskName.StartsWith($Prefix)) { $taskName.Substring($Prefix.Length) } else { $taskName }
    $j = $jobs | Where-Object { $_.Name -eq $short }
    if (-not $j) { throw "Keine VIPER-Aufgabe: '$taskName' (erlaubt: $(($jobs | ForEach-Object { $Prefix + $_.Name }) -join ', '))" }
    $j
}

function Get-ScheduleText($t) {
    $parts = foreach ($tr in $t.Triggers) {
        $class = $tr.CimClass.CimClassName
        $time = if ($tr.StartBoundary) { ([string]$tr.StartBoundary).Substring(11, 5) } else { '' }
        $fixed = if ([string]$tr.StartBoundary -match '([+-]\d\d:\d\d|Z)$') { ' (UTC-fest)' } else { '' }
        if ($class -like '*Logon*') { 'bei Anmeldung' }
        elseif ($class -like '*Daily*') { "taeglich $time$fixed" }
        elseif ($class -like '*Weekly*') { (($dayNames.Keys | Where-Object { $tr.DaysOfWeek -band $_ } | Sort-Object | ForEach-Object { $dayNames[$_] }) -join ',') + " $time$fixed" }
        else { $class }
    }
    $parts -join '; '
}

function Get-TaskRow($j) {
    $name = $Prefix + $j.Name
    $t = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if (-not $t) { return [pscustomobject]@{ Name = $name; Enabled = '-'; Schedule = 'nicht vorhanden'; NextRun = ''; Action = '' } }
    $i = Get-ScheduledTaskInfo -TaskName $name
    $exe = Split-Path -Leaf ([string]$t.Actions[0].Execute)
    $how = if ($exe -ieq 'pythonw.exe') { 'unsichtbar (pythonw)' } else { "$exe (zeigt ein Fenster)" }
    [pscustomobject]@{
        Name     = $name
        Enabled  = $(if ($t.State -eq 'Disabled') { 'nein' } else { 'ja' })
        Schedule = Get-ScheduleText $t
        NextRun  = $(if ($i.NextRunTime) { $i.NextRunTime.ToString('dd.MM.yyyy HH:mm') } else { '' })
        Action   = ("$how $([string]$t.Actions[0].Execute) $($j.Script) $($j.Args)").TrimEnd()
    }
}

function Invoke-Step($text, [scriptblock]$block) {
    if ($DryRun) { Write-Host "[DryRun] $text" } else { Write-Host $text; & $block }
}

switch ($Action) {
    'List' {
        $jobs | ForEach-Object { Get-TaskRow $_ } | Format-List | Out-String -Width 300
    }

    'Deploy' {
        Assert-SilentRuntime
        New-Item -ItemType Directory -Force (Join-Path $Root 'logs') | Out-Null
        foreach ($j in $jobs) {
            $name = $Prefix + $j.Name
            $actionParams = Get-ActionParams $j
            $existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
            $defaults = @()
            if ($j.Logon -ne $false) { $defaults += New-ScheduledTaskTrigger -AtLogOn -User $user }
            $defaults += @(New-TimeTriggers @($j.At) $j.Kind)
            if (-not $existing) {
                Invoke-Step "Lege an $name : $($actionParams.Execute) $($actionParams.Argument)" {
                    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
                    Register-ScheduledTask -TaskName $name -Action (New-ScheduledTaskAction @actionParams) -Trigger $defaults -Settings (New-Settings $j) -Principal $principal -Description "Trading ($Root): $($j.Script)" | Out-Null
                }
                continue
            }
            # vorhandene Aufgabe: Aktion und Einstellungen wie in der Tabelle, Startzeiten als Ortszeit; Zeitplan (Edit) und Aktiviert-Zustand bleiben
            $before = ($existing.Triggers | ForEach-Object { [string]$_.StartBoundary }) -join '|'
            $triggers = if ($ResetSchedule) { $defaults } else { @($existing.Triggers | ForEach-Object { if ($_.StartBoundary) { Set-LocalStart $_ } else { $_ } }) }
            $after = ($triggers | ForEach-Object { [string]$_.StartBoundary }) -join '|'
            $current = $existing.Actions[0]
            $same = ([string]$current.Execute -eq $actionParams.Execute) -and ([string]$current.Arguments -eq $actionParams.Argument) -and
                    ([string]$current.WorkingDirectory -eq [string]$actionParams.WorkingDirectory) -and ($before -eq $after) -and -not $ResetSchedule -and
                    $existing.Settings.StartWhenAvailable -and ([string]$existing.Settings.MultipleInstances -eq 'IgnoreNew')
            if ($same) { Write-Host "unveraendert: $name"; continue }
            Invoke-Step "Aktualisiere $name : $($actionParams.Execute) $($actionParams.Argument)" {
                Set-ScheduledTask -TaskName $name -Action (New-ScheduledTaskAction @actionParams) -Trigger $triggers -Settings (New-Settings $j) | Out-Null
            }
        }
    }

    'Edit' {
        if (-not $Task) { throw "Edit braucht -Task" }
        if ($Enable -and $Disable) { throw "-Enable und -Disable schliessen sich aus" }
        $j = Get-Job $Task
        $name = $Prefix + $j.Name
        $existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if (-not $existing) { throw "$name ist nicht angelegt (zuerst Deploy)" }
        if ($At) {
            $times = @($At -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
            foreach ($time in $times) { if ($time -notmatch '^([01]\d|2[0-3]):[0-5]\d$') { throw "Uhrzeit '$time': erwartet HH:MM" } }
            $kind = if ($Day) { $Day } elseif ($existing.Triggers | Where-Object { $_.CimClass.CimClassName -like '*Weekly*' }) { 'Weekly' } else { 'Daily' }
            $triggers = @($existing.Triggers | Where-Object { $_.CimClass.CimClassName -like '*Logon*' }) + @(New-TimeTriggers $times $kind)
            Invoke-Step "Zeitplan von $name : $kind $($times -join ', ') (Ortszeit)" { Set-ScheduledTask -TaskName $name -Trigger $triggers | Out-Null }
        }
        if ($Enable) { Invoke-Step "Aktiviere $name" { Enable-ScheduledTask -TaskName $name | Out-Null } }
        if ($Disable) { Invoke-Step "Deaktiviere $name" { Disable-ScheduledTask -TaskName $name | Out-Null } }
        if (-not ($At -or $Enable -or $Disable)) { Write-Host "Nichts zu aendern: -At, -Enable oder -Disable angeben" }
    }

    'Delete' {
        if (-not ($Task -or $All)) { throw "Delete braucht -Task oder -All" }
        $targets = if ($All) { $jobs } else { @(Get-Job $Task) }
        foreach ($j in $targets) {
            $name = $Prefix + $j.Name
            if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) { Invoke-Step "Loesche $name" { Unregister-ScheduledTask -TaskName $name -Confirm:$false } }
            else { Write-Host "nicht vorhanden: $name" }
        }
    }
}
