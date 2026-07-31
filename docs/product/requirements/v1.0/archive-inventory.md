# Archive inventory

| File                                                   | Role                                              | Mutable                             |
| ------------------------------------------------------ | ------------------------------------------------- | ----------------------------------- |
| `Hardware_Encyclopedia_Functional_Design_V1.0_CN.docx` | Byte-identical source requirement                 | No                                  |
| `SHA256SUMS`                                           | Independent integrity check                       | Only for new archive files          |
| `manifest.json`                                        | Machine-readable document and inspection metadata | Append-only by schema revision      |
| `README.md`                                            | Archive handling and renderer note                | Yes, without changing source claims |
| `requirements-summary.md`                              | Implementation-oriented baseline                  | Yes, with review                    |
| `scope-matrix.md`                                      | P0/P1/P2 boundary                                 | Yes, with product approval          |
| `domain-glossary.md`                                   | Shared language                                   | Yes, with domain review             |
| `risks-and-open-decisions.md`                          | Risk register and decision queue                  | Yes                                 |

The temporary LibreOffice PDF/PNG preview is intentionally absent. Its missing
Chinese glyphs were caused by local font availability and it is not an
authoritative representation of the document.
