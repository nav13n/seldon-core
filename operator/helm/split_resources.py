import argparse
import glob
import re
import yaml

parser = argparse.ArgumentParser()
parser.add_argument('--prefix', default="xx", help='find files matching prefix')
parser.add_argument('--folder', required=True, help='Output folder')
args, _ = parser.parse_known_args()

HELM_SPARTAKUS_IF_START = '{{- if .Values.usageMetrics.enabled }}\n'
HELM_CRD_IF_START = '{{- if .Values.crd.create }}\n'
HELM_NOT_SINGLE_NAMESPACE_IF_START = '{{- if not .Values.singleNamespace }}\n'
HELM_SINGLE_NAMESPACE_IF_START = '{{- if .Values.singleNamespace }}\n'
HELM_CONTROLLERID_IF_START = '{{- if .Values.controllerId }}\n'
HELM_NOT_CONTROLLERID_IF_START = '{{- if not .Values.controllerId }}\n'
HELM_RBAC_IF_START = '{{- if .Values.rbac.create }}\n'
HELM_RBAC_CSS_IF_START = '{{- if .Values.rbac.configmap.create }}\n'
HELM_SA_IF_START = '{{- if .Values.serviceAccount.create -}}\n'
HELM_CERTMANAGER_IF_START = '{{- if .Values.certManager.enabled -}}\n'
HELM_NOT_CERTMANAGER_IF_START = '{{- if not .Values.certManager.enabled -}}\n'
#HELM_SECRET_IF_START = '{{- if .Values.webhook.secretProvided -}}\n'
HELM_IF_END = '{{- end }}\n'

HELM_ENV_SUBST = {
    "AMBASSADOR_ENABLED": "ambassador.enabled",
    "AMBASSADOR_SINGLE_NAMESPACE": "ambassador.singleNamespace",
    "ENGINE_SERVER_GRPC_PORT": "engine.grpc.port",
    "ENGINE_CONTAINER_IMAGE_PULL_POLICY": "engine.image.pullPolicy",
    "ENGINE_LOG_MESSAGES_EXTERNALLY": "engine.logMessagesExternally",
    "ENGINE_SERVER_PORT": "engine.port",
    "ENGINE_PROMETHEUS_PATH": "engine.prometheus.path",
    "ENGINE_CONTAINER_USER": "engine.user",
    "ENGINE_CONTAINER_SERVICE_ACCOUNT_NAME": "engine.serviceAccount.name",
    "ISTIO_ENABLED":"istio.enabled",
    "ISTIO_GATEWAY":"istio.gateway",
    "PREDICTIVE_UNIT_SERVICE_PORT":"predictiveUnit.port"

}
HELM_VALUES_IMAGE_PULL_POLICY = '{{ .Values.image.pullPolicy }}'


def helm_value(value: str):
    return '{{ .Values.' + value + ' }}'

def helm_release(value: str):
    return '{{ .Release.' + value + ' }}'

if __name__ == "__main__":
    exp = args.prefix + "*"
    files = glob.glob(exp)
    webhookData = '{{- $altNames := list ( printf "seldon-webhook-service.%s" .Release.Namespace ) ( printf "seldon-webhook-service.%s.svc" .Release.Namespace ) -}}\n'
    webhookData = webhookData + '{{- $ca := genCA "custom-metrics-ca" 365 -}}\n'
    webhookData = webhookData + '{{- $cert := genSignedCert "seldon-webhook-service" nil $altNames 365 $ca -}}\n'

    for file in files:
        with open(file, 'r') as stream:
            res = yaml.safe_load(stream)
            kind = res["kind"].lower()
            name = res["metadata"]["name"].lower()
            filename = args.folder + "/" + (kind + "_" + name).lower() + ".yaml"

            print("Processing ",file)
            # Update common labels
            if "metadata" in res and "labels" in res["metadata"]:
                res["metadata"]["labels"]["app.kubernetes.io/instance"] = '{{ .Release.Name }}'
                res["metadata"]["labels"][
                    "app.kubernetes.io/name"] = '{{ include "seldon.name" . }}'
                res["metadata"]["labels"][
                    "app.kubernetes.io/version"] = '{{ .Chart.Version }}'

            # Update namespace to be helm var only if we are deploying into seldon-system
            if "metadata" in res and "namespace" in res["metadata"]:
                if res["metadata"]["namespace"] == "seldon-system" or res["metadata"]["namespace"] == "seldon1-system":
                    res["metadata"]["namespace"] = '{{ .Release.Namespace }}'

            # controller manager
            if kind == "deployment" and name == "seldon-controller-manager":
                res["spec"]["template"]["spec"]["containers"][0]["imagePullPolicy"] = helm_value(
                    'image.pullPolicy')
                res["spec"]["template"]["spec"]["containers"][0][
                    "image"] = "{{ .Values.image.registry }}/{{ .Values.image.repository }}:{{ .Values.image.tag }}"

                for env in res["spec"]["template"]["spec"]["containers"][0]["env"]:
                    if env["name"] in HELM_ENV_SUBST:
                        env["value"] = helm_value(HELM_ENV_SUBST[env["name"]])
                    elif env["name"] == "ENGINE_CONTAINER_IMAGE_AND_VERSION":
                        env[
                            "value"] = '{{ .Values.engine.image.registry }}/{{ .Values.engine.image.repository }}:{{ .Values.engine.image.tag }}'
                    elif env["name"] == "CONTROLLER_ID":
                        env["value"] = "{{ .Values.controllerId }}"
                # Update webhook port
                for portSpec in res["spec"]["template"]["spec"]["containers"][0]["ports"]:
                    if portSpec["name"] == "webhook-server":
                        portSpec["containerPort"] = helm_value("webhook.port")
                for argIdx in range(0, len(res["spec"]["template"]["spec"]["containers"][0]["args"])):
                    if res["spec"]["template"]["spec"]["containers"][0]["args"][argIdx] == "--webhook-port=443":
                        res["spec"]["template"]["spec"]["containers"][0]["args"][
                            argIdx] = "--webhook-port=" + helm_value("webhook.port")
                res["spec"]["template"]["spec"]["containers"][0]["args"].append("{{- if .Values.singleNamespace }}--namespace={{ .Release.Namespace }}{{- end }}")


            if kind == "serviceaccount" and name == "seldon-manager":
                res["metadata"]["name"] = helm_value("serviceAccount.name")

            if kind == "clusterrole":
                res["metadata"]["name"] = res["metadata"]["name"] + "-" + helm_release("Namespace")

            # Update cluster role bindings
            if kind == "clusterrolebinding":
                res["metadata"]["name"] = res["metadata"]["name"] + "-" + helm_release("Namespace")
                res["roleRef"]["name"] = res["roleRef"]["name"] + "-" + helm_release("Namespace")
                if name == "seldon-manager-rolebinding":
                    res["subjects"][0]["name"] = helm_value("serviceAccount.name")
                    res["subjects"][0]["namespace"] = helm_release("Namespace")
                elif name != "seldon-spartakus-volunteer":
                    res["subjects"][0]["namespace"] = helm_release("Namespace")

            # Update role bindings
            if kind == "rolebinding":
                res["subjects"][0]["namespace"] = helm_release("Namespace")
                if  name == "seldon1-manager-rolebinding" or name == "seldon1-manager-sas-rolebinding":
                    res["subjects"][0]["name"] = helm_value("serviceAccount.name")
                    res["subjects"][0]["namespace"] = helm_release("Namespace")


            # Update webhook certificates
            if name == "seldon-webhook-server-cert" and kind == "secret":
                res["data"]["ca.crt"] = "{{ $ca.Cert | b64enc }}"
                res["data"]["tls.crt"] = "{{ $cert.Cert | b64enc }}"
                res["data"]["tls.key"] = "{{ $cert.Key | b64enc }}"

            if kind == "mutatingwebhookconfiguration" or kind == "validatingwebhookconfiguration":
                res["metadata"]["name"] = res["metadata"]["name"] + "-" + helm_release("Namespace")
                res["webhooks"][0]["clientConfig"]["caBundle"] = "{{ $ca.Cert | b64enc }}"
                res["webhooks"][0]["clientConfig"]["service"]["namespace"] = helm_release("Namespace")
                if "certmanager.k8s.io/inject-ca-from" in res["metadata"]["annotations"]:
                    res["metadata"]["annotations"]["certmanager.k8s.io/inject-ca-from"] = helm_release("Namespace") + "/seldon-serving-cert"


            if kind == "certificate":
                res["spec"]["commonName"] = '{{- printf "seldon-webhook-service.%s.svc" .Release.Namespace -}}'
                res["spec"]["dnsNames"][0] = '{{- printf "seldon-webhook-service.%s.svc.cluster.local" .Release.Namespace -}}'

            if kind == "customresourcedefinition"and name == "seldondeployments.machinelearning.seldon.io":
                # Will only work for cert-manager at present as caBundle would need to be generated in same file as secrets above
                if "conversion" in res["spec"]:
                    res["spec"]["conversion"]["webhookClientConfig"]["caBundle"] = "=="
                if "certmanager.k8s.io/inject-ca-from" in res["metadata"]["annotations"]:
                    res["metadata"]["annotations"]["certmanager.k8s.io/inject-ca-from"] = helm_release("Namespace") + "/seldon-serving-cert"

            # Update webhook service port
            if kind == "service" and name == "seldon-webhook-service":
                res["spec"]["ports"][0]["targetPort"] = helm_value("webhook.port")


            fdata = yaml.dump(res, width=1000)

            # Spartatkus
            if name.find("spartakus") > -1:
                fdata =  HELM_SPARTAKUS_IF_START + fdata + HELM_IF_END
            # cluster roles for single namespace
            elif name == "seldon-manager-rolebinding" or name == "seldon-manager-role":
                fdata = HELM_NOT_SINGLE_NAMESPACE_IF_START + HELM_RBAC_IF_START + fdata + HELM_IF_END + HELM_IF_END
            elif name == "seldon-manager-sas-rolebinding" or name == "seldon-manager-sas-role":
                fdata = HELM_NOT_SINGLE_NAMESPACE_IF_START + HELM_RBAC_IF_START + HELM_RBAC_CSS_IF_START + fdata + HELM_IF_END + HELM_IF_END + HELM_IF_END
            # roles/rolebindings for single namespace
            elif name == "seldon1-manager-rolebinding" or name == "seldon1-manager-role":
                fdata = HELM_SINGLE_NAMESPACE_IF_START + HELM_RBAC_IF_START + fdata + HELM_IF_END + HELM_IF_END
            elif name == "seldon1-manager-sas-role" or name == "seldon1-manager-sas-rolebinding":
                fdata = HELM_SINGLE_NAMESPACE_IF_START + HELM_RBAC_IF_START  + HELM_RBAC_CSS_IF_START + fdata + HELM_IF_END + HELM_IF_END + HELM_IF_END
            # manager role binding
            elif name == "seldon-manager-cm-rolebinding" or name == "seldon-manager-cm-role":
                fdata = HELM_RBAC_IF_START + HELM_RBAC_CSS_IF_START + fdata + HELM_IF_END + HELM_IF_END
            elif name == "seldon-manager" and kind == "serviceaccount":
                fdata = HELM_SA_IF_START + fdata + HELM_IF_END
            elif kind == "issuer"or kind == "certificate":
                fdata = HELM_CERTMANAGER_IF_START + fdata + HELM_IF_END
            elif name == "seldon-webhook-server-cert" and kind == "secret":
                fdata = HELM_NOT_CERTMANAGER_IF_START + fdata + HELM_IF_END
            elif name == "seldondeployments.machinelearning.seldon.io":
                fdata =HELM_CRD_IF_START + fdata + HELM_IF_END

            # make sure webhook is not quoted as its an int
            fdata = fdata.replace("'{{ .Values.webhook.port }}'","{{ .Values.webhook.port }}")

            if not kind == "namespace":
                if "seldon1" in name and name != "seldon1-manager-rolebinding" and name != "seldon1-manager-role" and \
                    name != "seldon1-manager-sas-role" and name != "seldon1-manager-sas-rolebinding":
                    print("Ignore ",name)
                    continue
                elif name == "seldon-webhook-server-cert" and kind == "secret" or \
                        kind == "mutatingwebhookconfiguration" or kind == "validatingwebhookconfiguration":
                    webhookData = webhookData + "---\n\n" + fdata
                else:
                    with open(filename, 'w') as outfile:
                        outfile.write(fdata)
    # Write webhook related data in 1 file
    namespaceSelector = "  namespaceSelector:\n    matchLabels:\n      seldon.io/controller-id: " + helm_release("Namespace") + "\n"
    objectSelector = "  objectSelector:\n    matchLabels:\n      seldon.io/controller-id: " + helm_value("controllerId") + "\n"
    webhookData = re.sub(r"(.*namespaceSelector:\n.*matchExpressions:\n.*\n.*\n)",HELM_NOT_SINGLE_NAMESPACE_IF_START+r"\1"+HELM_IF_END+HELM_SINGLE_NAMESPACE_IF_START+namespaceSelector+HELM_IF_END,webhookData, re.M)
    webhookData = re.sub(r"(.*objectSelector:\n.*matchExpressions:\n.*\n.*\n)",HELM_NOT_CONTROLLERID_IF_START+r"\1"+HELM_IF_END+HELM_CONTROLLERID_IF_START+objectSelector+HELM_IF_END,webhookData, re.M)

    filename = args.folder + "/" + "webhook.yaml"
    with open(filename, 'w') as outfile:
        outfile.write(webhookData)