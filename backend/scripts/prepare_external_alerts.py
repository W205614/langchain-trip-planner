"""Render a private Alertmanager configuration; actual delivery is checked separately."""
import argparse
from pathlib import Path
from urllib.parse import urlsplit
from dotenv import dotenv_values
import yaml


def prepare(source, output):
    values=dotenv_values(source)
    receiver={"name":"external"}
    if values.get("ALERT_EMAIL_TO"):
        for name in ("SMTP_HOST","SMTP_FROM"):
            if not values.get(name):raise ValueError("Missing "+name)
        security=values.get("SMTP_SECURITY","starttls")
        if security!="starttls":raise ValueError("Alertmanager template uses STARTTLS; configure SMTP_SECURITY=starttls")
        mail={"to":values["ALERT_EMAIL_TO"],"from":values["SMTP_FROM"],
            "smarthost":values["SMTP_HOST"]+":"+str(values.get("SMTP_PORT") or "587"),"require_tls":True,"send_resolved":True}
        if values.get("SMTP_USERNAME"):
            mail.update(auth_username=values["SMTP_USERNAME"],auth_password=values.get("SMTP_PASSWORD", ""))
        receiver["email_configs"]=[mail]
    if values.get("ALERT_WEBHOOK_URL"):
        url=urlsplit(values["ALERT_WEBHOOK_URL"])
        if url.scheme!="https" or not url.hostname or url.username is not None or url.password is not None or url.fragment:
            raise ValueError("Alert webhook must be an HTTPS URL without embedded credentials")
        hook={"url":values["ALERT_WEBHOOK_URL"],"send_resolved":True}
        if values.get("ALERT_WEBHOOK_TOKEN"):
            hook["http_config"]={"authorization":{"credentials":values["ALERT_WEBHOOK_TOKEN"]}}
        receiver["webhook_configs"]=[hook]
    if len(receiver)==1:raise ValueError("Configure ALERT_EMAIL_TO or ALERT_WEBHOOK_URL")
    output=Path(output).resolve()
    if output.exists():raise ValueError("Refusing to overwrite existing alert configuration")
    output.parent.mkdir(parents=True,exist_ok=True)
    config={"global":{"resolve_timeout":"2m"},"route":{"receiver":"local-durable-inbox","group_by":["alertname","job"],
        "group_wait":"10s","group_interval":"2m","repeat_interval":"4h","routes":[{"receiver":"local-durable-inbox","continue":True},{"receiver":"external"}]},
        "receivers":[{"name":"local-durable-inbox","webhook_configs":[{"url":"http://alert-receiver:9088/alerts","send_resolved":True}]},receiver]}
    output.write_text(yaml.safe_dump(config,sort_keys=False),encoding="utf-8")
    output.chmod(0o600)
    return {"configured":True,"connection_checked":False,"private_file":str(output)}


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    print(prepare(args.env_file,args.output))
