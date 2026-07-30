-- ============================================================
-- SEED — Ghana universities list (uniRank 2026 A-Z listing)
-- ============================================================
-- Root cause of the empty signup dropdown originally: schema.sql only
-- ever inserted ONE row into `universities` (USTED, short_code
-- 'USTED'). Signup has since moved to free-text (any name, any
-- country, auto-created via get_or_create_university()), so this
-- seed data is no longer load-bearing for signup to function — but
-- it's still what populates the autocomplete suggestions in the
-- signup form's datalist, so worth having seeded.
--
-- Safe to run more than once — ON CONFLICT (short_code) DO NOTHING.
-- ============================================================

insert into universities (name, short_code) values
  ('Academic City University', 'ACU'),
  ('Accra Institute of Technology', 'AIT'),
  ('Accra Technical University', 'ATU'),
  ('African University of Communications and Business', 'AUCB'),
  ('All Nations University', 'ANU'),
  ('Anglican University College of Technology', 'AUCT'),
  ('Ashesi University', 'ASHESI'),
  ('BlueCrest College', 'BLUECREST'),
  ('Bolgatanga Technical University', 'BTU'),
  ('C.K. Tedam University of Technology and Applied Sciences', 'CKTU'),
  ('Cape Coast Technical University', 'CCTU'),
  ('Catholic Institute of Business and Technology', 'CIBT'),
  ('Catholic University of Ghana', 'CUG'),
  ('Central University', 'CU'),
  ('Christ Apostolic University College', 'CAUC'),
  ('Christian Service University', 'CSU'),
  ('Data Link Institute of Business and Technology', 'DLIBT'),
  ('Dominion University College', 'DUC'),
  ('Dr. Hilla Limann Technical University', 'DHLTU'),
  ('Evangelical Presbyterian University College', 'EPUC'),
  ('Garden City University College', 'GCUC'),
  ('Ghana Baptist University College', 'GBUC'),
  ('Ghana Christian University College', 'GHCUC'),
  ('Ghana Communication Technology University', 'GCTU'),
  ('Ghana Institute of Management and Public Administration', 'GIMPA'),
  ('Ho Technical University', 'HTU'),
  ('Islamic University College, Ghana', 'IUCG'),
  ('Jayee University College', 'JUC'),
  ('KAAF University College', 'KAAF'),
  ('Kessben University College', 'KESSBEN'),
  ('Kings University College', 'KUC'),
  ('Knutsford University College', 'KNUC'),
  ('Koforidua Technical University', 'KTU'),
  ('Kumasi Technical University', 'KSTU'),
  ('Kwame Nkrumah University of Science and Technology', 'KNUST'),
  ('Lancaster University, Ghana', 'LUG'),
  ('Maranatha University College', 'MUC'),
  ('Marshalls University College', 'MARSHALLS'),
  ('Methodist University', 'METHODIST'),
  ('Mountcrest University College', 'MOUNTCREST'),
  ('Palm University College', 'PALM'),
  ('Pentecost University', 'PENTECOST'),
  ('Perez University College', 'PEREZ'),
  ('Presbyterian University, Ghana', 'PUG'),
  ('Radford University College', 'RADFORD'),
  ('Regent University College of Science and Technology', 'REGENT'),
  ('Regional Maritime University', 'RMU'),
  ('Simon Diedong Dombo University of Business and Integrated Development Studies', 'SDDUBIDS'),
  ('Spiritan University College', 'SPIRITAN'),
  ('Sunyani Technical University', 'STU'),
  ('Takoradi Technical University', 'TTU'),
  ('Tamale Technical University', 'TATU'),
  ('University College of Agriculture and Environmental Studies', 'UCAES'),
  ('University College of Management Studies', 'UCOMS'),
  ('University for Development Studies', 'UDS'),
  ('University of Cape Coast', 'UCC'),
  ('University of Education, Winneba', 'UEW'),
  ('University of Energy and Natural Resources', 'UENR'),
  ('University of Environment and Sustainable Development', 'UESD'),
  ('University of Ghana', 'UG'),
  ('University of Health and Allied Sciences', 'UHAS'),
  ('University of Media, Arts and Communication', 'UNIMAC'),
  ('University of Mines and Technology', 'UMAT'),
  ('University of Professional Studies, Accra', 'UPSA'),
  ('Valley View University', 'VVU'),
  ('West End University College', 'WEUC'),
  ('Wisconsin International University College', 'WIUC'),
  ('Zenith University College', 'ZUC')
on conflict (short_code) do nothing;
