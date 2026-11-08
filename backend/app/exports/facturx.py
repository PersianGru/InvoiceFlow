"""Factur-X / ZUGFeRD PDF: the CII XML attached to a PDF as ``factur-x.xml`` (AFRelationship Data)
with the Factur-X XMP extension block. Not validated as PDF/A-3."""

from __future__ import annotations

import io

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, NameObject, TextStringObject

from app.exports.cii_writer import to_cii
from app.exports.pdf_summary import render_pdf_summary
from app.schemas.invoice import Invoice

XMP_TEMPLATE = """<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about="" xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/">
   <pdfaid:part>3</pdfaid:part><pdfaid:conformance>B</pdfaid:conformance>
  </rdf:Description>
  <rdf:Description rdf:about="" xmlns:fx="urn:factur-x:pdfa:CrossIndustryDocument:invoice:1p0#">
   <fx:DocumentType>INVOICE</fx:DocumentType>
   <fx:DocumentFileName>factur-x.xml</fx:DocumentFileName>
   <fx:Version>1.0</fx:Version>
   <fx:ConformanceLevel>EN 16931</fx:ConformanceLevel>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>"""


def to_facturx_pdf(invoice: Invoice, original_pdf: bytes | None) -> bytes:
    """Returns a PDF carrying ``factur-x.xml``. Uses the original PDF when there is one (and it is
    not already a Factur-X file), else a generated summary page."""
    xml = to_cii(invoice)
    base = original_pdf or render_pdf_summary(invoice)
    reader = PdfReader(io.BytesIO(base))
    writer = PdfWriter()
    writer.append(reader)
    attachment = writer.add_attachment("factur-x.xml", xml)
    attachment.associated_file_relationship = NameObject("/Data")
    attachment.subtype = NameObject("/text#2Fxml")
    attachment.description = TextStringObject("Factur-X invoice data (EN 16931)")
    writer.add_metadata({"/Producer": "InvoiceFlow", "/Title": f"Invoice {invoice.number or invoice.id}"})
    writer.xmp_metadata = XMP_TEMPLATE.encode("utf-8")
    root = writer.root_object
    af = root.get("/AF")
    if af is None:
        af = ArrayObject()
        root[NameObject("/AF")] = af
    af.append(attachment.pdf_object.indirect_reference or attachment.pdf_object)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
