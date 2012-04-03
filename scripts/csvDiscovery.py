#!/usr/bin/env python

import Globals
import sys
from os.path import isfile
import time
import DateTime
import csv
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase

from Products.ZenUtils.ZenScriptBase import ZenScriptBase
from Products.ZenUtils.Utils import binPath
from Products.Jobber.jobs import ShellCommandJob

def rowDiscovery(row,dmd,conn):
  t1=time.time()
  organizer=dmd.Networks.findNet(row[0])
  if organizer==None:
    organizer=dmd.Networks.createNet(row[0])
  cmd = ["zendisc", "run", "--now", "--net", organizer.getNetworkName(),"--deviceclass",row[1]]
  if getattr(organizer, "zSnmpStrictDiscovery", False):
      cmd += ["--snmp-strict-discovery"]
  if getattr(organizer, "zPreferSnmpNaming", False):
      cmd += ["--prefer-snmp-naming"]
  zd = binPath('zendisc')
  zendiscCmd = [zd] + cmd[1:]
  status = dmd.JobManager.addJob(ShellCommandJob, zendiscCmd)
  while not status.isFinished():
    time.sleep(3)
    conn.syncdb()
  t2=time.time()
  devs=[ dev for dev in dmd.Devices.getOrganizer(row[1]).getSubDevices() if t1<DateTime.DateTime(dev.getCreatedTimeString())<t2 ]
  print 'Discovery time: %s, discovered %s new devices'%((t2-t1),len(devs))
  [rowtime,distribution]=moveDiscoveredDevices(row,devs,dmd,conn)
  return [rowtime,time.time()-t1,len(devs),distribution]

def moveDiscoveredDevices(row,devs,dmd,conn):
  deviceTimes={}
  distribution={}
  for d in [ dev for dev in devs if dev.getPingStatus()==0 ]:
    t1=time.time()
    conn.syncdb()
    if row[4]!='':
      d.setLocation('%s'%row[4])
    if row[2]!='':
      if 'Windows' in d.getOSProductName():
        if 'XP' in d.getOSProductName():
          devclass='%s/XP'%row[2]
        elif 'Windows 7':
          devclass=row[2]
        if devclass: 
          o=dmd.Devices.createOrganizer(devclass)
          d.changeDeviceClass(devclass)
        print 'Modeling the device %s once moved to %s'%(d.id,d.getDeviceClassName())
        d.collectDevice(setlog=False,background=False)
      else:
        if 'Canon' in d.getOSProductName():
          devclass='/Printer/Canon'
        elif 'HP' in d.getOSProductName():
          devclass='/Printer/HP'
        elif 'RICOH' in d.getHWManufacturerName():
          devclass='/Printer/RICOH'
        elif 'SEIKO' in d.getHWManufacturerName():
          devclass='/Printer/SEIKO'
        else:
          devclass=row[3]
        o=dmd.Devices.createOrganizer(devclass)
        d.changeDeviceClass(devclass)
    deviceTimes[d.id]=time.time()-t1
    if distribution.has_key(devclass):
      distribution[devclass]+=1
    else:
      distribution[devclass]=1
  return (deviceTimes,distribution)

def printStats(discoveryTimes,dmd):
  print 'Finished all discoveries.'
  print 'Total discovery time (for all discoveries): %s'%(time.time()-t1)
  print 'Time for each discovery:'
  for k in discoveryTimes.keys():
    print '\tDiscovery %s\t\t total time %s (discovery, first modelation and, only when needed, movement and remodel)'%(k,discoveryTimes[k][1])
    for rk in discoveryTimes[k][0].keys():
      print '\t\tDevice %s (%s)\t\tTime %s (movement and remodel)'%(rk,dmd.Devices.findDevice(rk).getHWTag(),discoveryTimes[k][0][rk])

def sendCSVResults(discoveryTimes,dmd,group,taskname):
  path='/opt/zenoss/log/discovery'
  filename=("%s.%s.csv"%(time.strftime('%Y%m%d.%H%M%S'),taskname))
  file=open('%s/%s'%(path,filename), "wb")
  c = csv.writer(file,delimiter=";",quotechar="\"")
  c.writerow(["Device Name","IP Address","Tag","Serial Number","Device Class","Production State","Location","HW Manufacturer","HW Product","OS Manufacturer", "OS Product"])
  dccounter={}
  dcounter=0
  for k in discoveryTimes.keys():
    for rk in discoveryTimes[k][0].keys():
      d=dmd.Devices.findDevice(rk)
      device_type=d.getDeviceClassName().split('/')[1]
      if not dccounter.has_key(device_type):
        dccounter[device_type]=1
      else:
        dccounter[device_type]+=1  
      dcounter+=1
      try:
        row=[d.name(),d.manageIp,d.getHWTag(),d.getHWSerialNumber(),d.getDeviceClassName(),d.getProdState(),d.getLocationName(),
          d.getHWManufacturerName(),d.getHWProductName(),d.getOSManufacturerName(),d.getOSProductName()]
      except Exception,e:
        print 'CSV Error: %s'%e
        pass
      c.writerow(row)
  file.close()
  try:
    mail=",".join([ u.getEmailAddresses()[0] for u in dmd.ZenUsers.findObject('G_Discovery').getMemberUserSettings() ])
    msg = MIMEMultipart('alternative')
    
    msg['From']="blabla@gmail.com"
    msg['To']=mail
    
    if dcounter > 0:
      msg['Subject']="[Zenoss Discovery] Task %s: New devices found (%s)"%(taskname,dcounter)
      text = "%s discovery: new devices (%s) list is attached in CSV format.\n\n"%(taskname,dcounter)
      html  = "<html><head><style>body {font:normal 13px arial, helvetica,tahoma,sans-serif; }\ntd, th { border: 1px #ccc solid;text-align:right; }\ntable { font:normal 12px arial, helvetica,tahoma,sans-serif; padding: 1px; border-collapse: collapse;}\nth{background-color:#ccf;}\n</style></head><body><p><i>%s</i> discovery: new devices list is attached in CSV format.</p>"%taskname
      html += "<br><br><p>On %s, the discovery task \"%s\" discovered %s devices:</p>"%(time.strftime('%Y/%m/%d at %H:%M'),taskname,dcounter)
      html += "<table width=50%><tr><th>Device Class</th><th>Discovered devices</th></tr>"
      for deviceClass in sorted(dccounter.keys()):
        html += "<tr><td>%s</td><td>%s</td></tr>"%(deviceClass,dccounter[deviceClass])
      html += "</table><br><table width=75%><tr><th>Network Range</th><th>Device Class</th><th>Number of devices</th></tr>"
      for i in sorted(discoveryTimes.keys()):
        html += "<tr style=\"background-color:#bbc;\"><td>%s<br>%s</td><td>&nbsp;<br>All device classes</td><td><b>%s</b></td></tr>"%(dmd.Networks.findNet(i).description,i,len(discoveryTimes[i][0]))
        for j in sorted(discoveryTimes[i][3].keys()):
          html += "<tr><td>&nbsp;</td><td>%s</td><td>%s</td></tr>"%(j,discoveryTimes[i][3][j])
      html += "</table></body></html>"
      fp = open('%s/%s'%(path,filename), 'rb')
      attachment = MIMEBase('text','csv')
      attachment.set_payload(fp.read())
      attachment.add_header('Content-Disposition', 'attachment', filename=filename)
      fp.close()
      msg.attach(attachment)
    else:
      msg['Subject']="[Zenoss Discovery] Task %s: No new devices "%(ndev,taskname)
      text = "%s discovery: no new devices discovered."%taskname
      html  = "<html><body><p><i>%s</i> discovery: no new devices discovered.</p></body></html>"%taskname

    msg.attach(MIMEText(text, 'plain'))
    msg.attach(MIMEText(html, 'html'))

    s=smtplib.SMTP('localhost')
    s.sendmail(msg['From'],msg['To'],msg.as_string())
    s.quit()
  except Exception,e:
    print 'CSV Mail Send Error: %s'%e
    pass

#sendCSVResults(discoveryTimes,dmd,'G_Discovery',taskname)
connection=ZenScriptBase(connect=True)
dmd = connection.dmd
taskname,csvpath=sys.argv[1:]
ndev=0
#csvpath='/etc/cron.zenoss/files/NetworkRanges.csv'
#taskname='TESTPR'
t1=time.time()
if not isfile(csvpath):
  print 'csvpath %s is not a file!'%csvpath
else:
  discoveryTimes={}
  fileReader=csv.reader(open(csvpath,'r'), delimiter=';', quotechar='"')
  for row in fileReader:
    if row[0]==taskname:
      row=row[1:]
      print 'Discovery: %s'%row
      print 'Waiting for Zenoss job to finish...'
      discoveryTimes[row[0]]=rowDiscovery(row,dmd,connection)
      ndev+=discoveryTimes[row[0]][2]
  printStats(discoveryTimes,dmd)
  if ndev>0:
    sendCSVResults(discoveryTimes,dmd,'G_Discovery',taskname)
