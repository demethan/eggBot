#!/usr/bin/env python

from crontab import CronTab

my_cron =  CronTab(tabfile='crontab.tab')
schedule = []


schedule = [(job.command.split()[3], job.description()) for job in my_cron if "restart" in job.command ]

print(schedule)
# for job in my_cron:
# 	if "restart" in job.command:
# 		raw_line = job.command.split()
# 		time = job.description()

# 		print(raw_line[3]+":"+time)
# 	blah = (raw_line[3], time)
