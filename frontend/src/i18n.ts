// Screen labels in English or Hindi. The English text is the key, so untranslated text simply shows in English.
// Tender values, clause text and names are never translated. Standard tender terms (EMD, BOQ, JV, NIT, GST, PAN)
// stay in English, as in Indian tender documents.
export type Lang = "en" | "hi";
const KEY = "tp_lang";

function read(): Lang {
  try { return localStorage.getItem(KEY) === "hi" ? "hi" : "en"; } catch { return "en"; }
}

export const lang: Lang = read();

export function setLang(l: Lang) {
  try { localStorage.setItem(KEY, l); } catch { /* storage blocked: stays English */ }
  window.location.reload(); // every label is re-rendered in the new language
}

const HI: Record<string, string> = {
  // navigation and sign-in
  "Tenders": "निविदाएँ", "Company vault": "कंपनी दस्तावेज़ भंडार", "Settings & audit": "सेटिंग्स और ऑडिट", "Sign out": "साइन आउट",
  "Sign in": "साइन इन", "Sign in to your bid workspace.": "अपने बोली कार्यक्षेत्र में साइन इन करें।", "Email": "ईमेल", "Password": "पासवर्ड",
  "Organization name": "संस्था का नाम", "Your name": "आपका नाम", "Create organization": "संस्था बनाएँ",
  "Create your organization and the first administrator account.": "अपनी संस्था और पहला प्रशासक खाता बनाएँ।",
  "At least 10 characters.": "कम से कम 10 अक्षर।", "Fill in for me": "मेरे लिए भरें",
  "Demo version with made-up (SYNTHETIC) data": "काल्पनिक (SYNTHETIC) डेटा वाला डेमो संस्करण",
  // tender list
  "New tender": "नई निविदा", "Every bid in preparation, with the most urgent first.": "तैयारी में सभी बोलियाँ, सबसे ज़रूरी पहले।",
  "Search title, tender ID or department": "शीर्षक, निविदा आईडी या विभाग खोजें", "In preparation": "तैयारी में",
  "Submitted": "जमा की गई", "All tenders": "सभी निविदाएँ", "Tender": "निविदा", "Bidder": "बोलीदाता", "Deadline": "अंतिम तिथि",
  "Open blockers": "खुली बाधाएँ", "Checklist closed": "चेकलिस्ट पूर्ण", "Not chosen": "चुना नहीं गया", "Deadline passed": "अंतिम तिथि निकल गई",
  "No tenders yet. Create one and upload the tender volumes to begin.": "अभी कोई निविदा नहीं। शुरू करने के लिए नई निविदा बनाएँ और निविदा खंड अपलोड करें।",
  "No tenders match this filter.": "इस फ़िल्टर से कोई निविदा नहीं मिली।",
  // tender tabs
  "Command centre": "नियंत्रण केंद्र", "Documents": "दस्तावेज़", "Clauses": "खंड (क्लॉज़)", "How to qualify": "अर्हता कैसे पाएँ",
  "Eligibility": "पात्रता", "Checklist": "चेकलिस्ट", "Document studio": "दस्तावेज़ स्टूडियो", "BOQ and quote": "BOQ और दर",
  "Envelopes and export": "लिफ़ाफ़े और निर्यात",
  // workflow steps
  "Create tender": "निविदा बनाएँ", "Upload all volumes": "सभी खंड अपलोड करें", "Verify extraction": "पढ़े गए विवरण जाँचें",
  "Review clauses and amendments": "खंड और संशोधन जाँचें", "Choose bidder or JV": "बोलीदाता या JV चुनें", "Assess eligibility": "पात्रता आँकें",
  "Collect evidence": "साक्ष्य एकत्र करें", "Prepare technical response": "तकनीकी उत्तर तैयार करें", "Estimate and approve prices": "दरें आँकें और स्वीकृत करें",
  "Generate forms": "फ़ॉर्म बनाएँ", "Sign or certify externally": "बाहर हस्ताक्षर / प्रमाणन कराएँ", "Verify returned documents": "लौटे दस्तावेज़ जाँचें",
  "Assemble envelopes": "लिफ़ाफ़े तैयार करें", "Run preflight": "अंतिम जाँच चलाएँ", "Export submission files": "जमा करने की फ़ाइलें निर्यात करें",
  "Record portal receipt": "पोर्टल रसीद दर्ज करें",
  // panels and sections
  "Upload tender volumes": "निविदा खंड अपलोड करें", "Page completeness": "पृष्ठ पूर्णता", "Document register": "दस्तावेज़ रजिस्टर",
  "Order of precedence": "प्राथमिकता क्रम", "A newer tender reader is available": "निविदा रीडर का नया संस्करण उपलब्ध है",
  "Templates": "टेम्पलेट", "Generated documents": "बनाए गए दस्तावेज़", "Records": "रिकॉर्ड", "Documents and credentials": "दस्तावेज़ और प्रमाणपत्र",
  "Legal entities": "विधिक इकाइयाँ", "Joint ventures": "संयुक्त उद्यम (JV)", "Forms and letters": "फ़ॉर्म और पत्र",
  "Technical compliance": "तकनीकी अनुपालन", "Compliance matrix": "अनुपालन तालिका", "Cross-document consistency": "दस्तावेज़ों में एकरूपता",
  "Quote versions and approval": "दर संस्करण और स्वीकृति", "Original document": "मूल दस्तावेज़",
  "Document type": "दस्तावेज़ का प्रकार", "Detect automatically": "स्वतः पहचानें",
  // statuses
  "Pass": "पास", "Fail": "फ़ेल", "Insufficient evidence": "अपर्याप्त साक्ष्य", "Ambiguous": "अस्पष्ट", "Not applicable": "लागू नहीं",
  "Processed": "संसाधित", "Unreadable": "पढ़ने योग्य नहीं", "Failed": "विफल", "Duplicate": "डुप्लिकेट", "Excluded": "बाहर रखा",
  "Pending": "लंबित", "Queued": "कतार में", "Running": "चल रहा है", "Done": "पूर्ण", "Cancelled": "रद्द", "Needs password": "पासवर्ड चाहिए",
  "Choose type": "प्रकार चुनें", "Needs conversion": "रूपांतरण चाहिए", "ZIP expanded": "ZIP खोला गया", "Rejected": "अस्वीकृत",
  "Uploaded": "अपलोड हुआ", "Processing": "संसाधन जारी", "Unreviewed": "समीक्षा बाकी", "Needs review": "समीक्षा आवश्यक", "Approved": "स्वीकृत",
  "Awaiting confirmation": "पुष्टि की प्रतीक्षा", "Superseded": "प्रतिस्थापित", "Verified": "सत्यापित", "Unverified": "असत्यापित",
  "Open": "खुला", "In progress": "प्रगति में", "Accepted": "स्वीकार", "Draft": "मसौदा", "Awaiting signature": "हस्ताक्षर की प्रतीक्षा",
  "Awaiting certification": "प्रमाणन की प्रतीक्षा", "Signed upload received": "हस्ताक्षरित प्रति प्राप्त", "Estimator reviewed": "आकलनकर्ता ने जाँचा",
  "Invalidated": "अमान्य", "Final-ready": "अंतिम रूप से तैयार", "Frozen": "फ़्रीज़", "Check": "जाँचें", "Blocked": "रुका हुआ",
  "Critical": "गंभीर", "High": "उच्च", "Medium": "मध्यम", "Complies": "अनुपालन करता है", "Deviation": "विचलन", "Exclusion": "अपवर्जन",
  "Qualifies": "अर्हता प्राप्त", "Qualifies, confirm rules": "अर्हता प्राप्त, नियम पुष्ट करें", "Gaps to close": "कमियाँ दूर करें",
  "Does not qualify": "अर्हता नहीं", "Confirmed": "पुष्ट", "From JV clause": "JV खंड से", "Assumed": "अनुमानित",
  // common
  "Refresh": "रीफ़्रेश", "Loading…": "लोड हो रहा है…", "Try again": "फिर कोशिश करें", "Cancel": "रद्द करें", "Close": "बंद करें",
  "Re-read with the latest reader": "नए रीडर से दोबारा पढ़ें", "Save ranks": "क्रम सहेजें", "Scenarios": "परिदृश्य",
  "Clean up scan": "स्कैन सुधारें", "Replace": "बदलें", "View": "देखें", "Verify": "सत्यापित करें", "Reject": "अस्वीकार करें",
  "Generate": "बनाएँ", "Map fields": "फ़ील्ड मिलाएँ", "Place boxes": "बॉक्स लगाएँ", "Preview": "पूर्वावलोकन",
};

/** Translate a screen label (returns the English text when no Hindi label exists). */
export function t(s: string): string {
  return lang === "hi" ? HI[s] ?? s : s;
}
