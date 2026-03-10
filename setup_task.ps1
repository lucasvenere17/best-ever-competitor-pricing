$action = New-ScheduledTaskAction -Execute "C:\Users\lucas\Documents\best-ever-competitor-pricing\run_scraper.bat"
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At 6:00AM
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName "BestEverPricingScraper" -Action $action -Trigger $trigger -Settings $settings -Description "Weekly competitor pricing scraper" -Force
Write-Host "Task created successfully!"
